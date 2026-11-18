from django.db import transaction
from django.db.models import Value
from django.db.models.functions import Lower, Coalesce

from services.models import Product, ProductVariant
from shop.models import ShopActivityLog
import logging

logger = logging.getLogger(__name__)


def restore_product_variant(viewset, log, request):
    """
    Restore a soft-deleted ProductVariant.

    Validations (mirroring create/add-variant flow):
      - Parent product must be active (not soft-deleted); restore the product first
      - external_qr_code must not already belong to another active variant in the shop
        — mirrors the unique_external_qr_per_shop partial DB constraint
      - Attribute must not already exist on another active variant of the same product
        — case-insensitive, mirrors ProductSerializer.validate_variants duplicate check
    """
    shop_id = viewset.shop_id
    variant_id = log.object_id

    variant = (
        ProductVariant.all_objects
        .select_related("product__shop_service__shop")
        .filter(id=variant_id, shop_id=shop_id, deleted__isnull=False)
        .first()
    )
    if not variant:
        return viewset.not_found_response(
            resource="ProductVariant",
            message="Deleted product variant not found or already restored."
        )

    # Validate: parent product must be active before restoring a variant
    parent_deleted = (
        Product.all_objects
        .filter(pk=variant.product_id, deleted__isnull=False)
        .exists()
    )
    if parent_deleted:
        return viewset.validation_error_response(
            errors={"product": ["The parent product has been deleted. Restore the product first."]},
            message="Cannot restore: the parent product has been deleted."
        )

    try:
        with transaction.atomic():
            # Lock to prevent concurrent restore races on the same variant
            variant = (
                ProductVariant.all_objects
                .select_for_update()
                .select_related("product__shop_service__shop")
                .get(pk=variant.pk)
            )
            shop = variant.product.shop_service.shop

            # Validate: external_qr_code uniqueness
            # Mirrors unique_external_qr_per_shop (active records only, non-null codes only)
            if variant.external_qr_code:
                qr_conflict = ProductVariant.objects.filter(
                    shop_id=shop_id,
                    external_qr_code=variant.external_qr_code,
                    deleted__isnull=True,
                ).exists()
                if qr_conflict:
                    return viewset.validation_error_response(
                        errors={"external_qr_code": ["QR code already exists."]},
                        message="Cannot restore: QR code is already in use by another variant."
                    )

            # Validate: attribute uniqueness (case-insensitive) across active variants of same product
            # Mirrors ProductSerializer.validate_variants duplicate-attribute check
            attr = (variant.attribute or '').strip().lower()
            attr_conflict = (
                ProductVariant.objects
                .filter(product=variant.product, deleted__isnull=True)
                .annotate(_attr=Lower(Coalesce('attribute', Value(''))))
                .filter(_attr=attr)
                .exists()
            )
            if attr_conflict:
                return viewset.validation_error_response(
                    errors={"attribute": ["A variant with the same attribute already exists."]},
                    message="Cannot restore: a variant with the same attribute already exists."
                )

            # Restore variant (bypass custom save() to avoid unintended structure_updated_at side-effects)
            ProductVariant.all_objects.filter(pk=variant.pk).update(deleted=None)

            ShopActivityLog.objects.create(
                shop=shop,
                user=request.user,
                content_type=log.content_type,
                object_id=variant.id,
                action="restored",
                log_message=f"Variant '{variant.attribute or 'default'}' restored.",
                restored_from_activity=log,
            )

        return viewset.success_response(
            data={"variant_id": variant.id},
            message="Product variant restored successfully."
        )

    except Exception as e:
        logger.error(f"Failed to restore product variant {variant_id}: {e}", exc_info=True)
        return viewset.error_response(
            message="Unable to restore product variant. Please try again later.",
            dev_message=str(e)
        )
