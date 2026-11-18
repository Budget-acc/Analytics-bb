from django.db import transaction
from django.db.models import Value
from django.db.models.functions import Lower, Coalesce

from services.models import Product, ProductVariant
from services.api.v3.views.product.constants import PRODUCT_PLAN_DEFAULT_LIMIT
from shop.models import ShopActivityLog, Shop
import logging

logger = logging.getLogger(__name__)


def restore_product(viewset, log, request):
    """
    Restore a soft-deleted Product and cascade-restore its soft-deleted variants.

    Validations (mirroring create flow):
      - Associated ShopOwnerService must still be active
      - Active product count must not exceed the shop's plan limit
      - No active product in the same service with matching (name, color, model, category)
        — case-insensitive, mirrors the unique_product_ci_per_shop DB constraint
      - No active variant in the shop already holds a QR code that belongs to any
        cascade-deleted variant being restored
    """
    shop_id = viewset.shop_id
    product_id = log.object_id

    product = (
        Product.all_objects
        .select_related("shop_service__shop")
        .filter(id=product_id, shop_service__shop_id=shop_id, deleted__isnull=False)
        .first()
    )
    if not product:
        return viewset.not_found_response(
            resource="Product",
            message="Deleted product not found or already restored."
        )

    try:
        with transaction.atomic():
            # Lock to prevent concurrent restore races on the same product
            product = (
                Product.all_objects
                .select_for_update()
                .select_related("shop_service__shop")
                .get(pk=product.pk)
            )
            shop_service = product.shop_service
            # Serialize product-limit checks per shop to avoid concurrent over-restores.
            shop = Shop.objects.select_for_update().get(pk=shop_service.shop_id)

            # Validate: associated service must still be active
            if shop_service.deleted is not None:
                return viewset.validation_error_response(
                    errors={"shop_service": ["The service this product belongs to has been deleted."]},
                    message="Cannot restore: the associated service has been deleted."
                )

            # Validate: product plan limit
            active_count = Product.objects.filter(
                shop_service__shop_id=shop_id,
                deleted__isnull=True,
            ).count()
            effective_limit = PRODUCT_PLAN_DEFAULT_LIMIT + shop.extra_stock_limit
            if active_count >= effective_limit:
                return viewset.error_response(
                    message=f"Product limit reached ({effective_limit}). Cannot restore product."
                )

            # Validate: case-insensitive name/color/model/category uniqueness.
            # Mirrors unique_product_ci_per_shop: Lower(Coalesce(field, '')) per column.
            name_conflict = (
                Product.objects
                .annotate(
                    _name=Lower(Coalesce('name', Value(''))),
                    _color=Lower(Coalesce('color', Value(''))),
                    _model=Lower(Coalesce('model', Value(''))),
                    _category=Lower(Coalesce('category', Value(''))),
                )
                .filter(
                    shop_service=shop_service,
                    deleted__isnull=True,
                    _name=(product.name or '').lower(),
                    _color=(product.color or '').lower(),
                    _model=(product.model or '').lower(),
                    _category=(product.category or '').lower(),
                )
                .exists()
            )
            if name_conflict:
                return viewset.validation_error_response(
                    errors={"name": ["A product with the same name already exists in this service."]},
                    message="A product with the same name already exists in this service."
                )

            # Validate: QR codes of cascade-deleted variants must not conflict with active variants
            variants_to_restore = list(
                ProductVariant.all_objects
                .filter(product=product, deleted__isnull=False)
                .values('id', 'external_qr_code')
            )
            qr_codes = [v['external_qr_code'] for v in variants_to_restore if v['external_qr_code']]
            if qr_codes:
                qr_conflict = ProductVariant.objects.filter(
                    shop=shop,
                    external_qr_code__in=qr_codes,
                    deleted__isnull=True,
                ).exists()
                if qr_conflict:
                    return viewset.validation_error_response(
                        errors={"external_qr_code": ["QR code already exists."]},
                        message="Cannot restore: a variant's QR code is already in use."
                    )

            # Restore product
            Product.all_objects.filter(pk=product.pk).update(deleted=None)

            # Cascade-restore all soft-deleted variants of this product
            if variants_to_restore:
                ProductVariant.all_objects.filter(
                    product=product, deleted__isnull=False
                ).update(deleted=None)

            ShopActivityLog.objects.create(
                shop=shop,
                user=request.user,
                content_type=log.content_type,
                object_id=product.id,
                action="restored",
                log_message=f"Product '{product.name}' restored.",
                restored_from_activity=log,
            )

        return viewset.success_response(
            data={"product_id": product.id},
            message="Product restored successfully."
        )

    except Exception as e:
        logger.error(f"Failed to restore product {product_id}: {e}", exc_info=True)
        return viewset.error_response(
            message="Unable to restore product. Please try again later.",
            dev_message=str(e)
        )
