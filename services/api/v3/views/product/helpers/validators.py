from django.db.models import Subquery
from rest_framework.exceptions import ValidationError
from services.models import ProductVariant


def _validate_external_qr_codes(self, shop, variants_data):
    """
    Shop-level external_qr_code validation (best possible without denormalization)
    """
    codes = []
    for idx, v in enumerate(variants_data):
        code = v.get("external_qr_code")
        if code:
            if not isinstance(code, str) or not code.strip():
                raise ValidationError({
                    f"variants[{idx}][external_qr_code]": ["Must be a non-empty string."]
                })
            code = code.strip()
            if len(code) > 128:
                raise ValidationError({
                    f"variants[{idx}][external_qr_code]": ["Max length is 128 characters."]
                })
            codes.append(code)

    if not codes:
        return

    # In-request duplication check
    if len(codes) != len(set(codes)):
        raise ValidationError({
            "external_qr_code": ["Code already exists."]
        })

    # DB constraint enforces uniqueness — plain lookup for friendly error message
    conflicts = (
        ProductVariant.objects
        .filter(
            external_qr_code__in=codes,
            product__shop_service__shop=shop,
            deleted__isnull=True
        )
        .values_list("external_qr_code", flat=True)
    )

    if conflicts:
        raise ValidationError({
            "external_qr_code": ["Code already exists."]
        })

def _validate_registration_number_uniqueness(
    self,
    shop_id,
    validated_attributes,
    product=None
):
    """
    Validate registration number uniqueness among ACTIVE products only,
    scoped to the authenticated shop (shop_id from the request).

    Args:
        shop_id: Integer shop ID from get_active_shop_id(request). Must always be
                 the authenticated shop — NOT derived from shop_service.shop, which
                 may belong to a different shop when services are shared.
        validated_attributes: List of {'template': ..., 'value': ...} dicts.
        product: Product instance to exclude on partial update (None for create).
    """

    from services.models import ServiceAttributeValue, Product

    reg_entry = next(
        (
            a for a in validated_attributes
            if a['template'].attribute_key == 'registration_number'
        ),
        None
    )

    if not reg_entry:
        return

    reg_value = reg_entry['value']

    if reg_value in [None, "", []]:
        return

    # Subquery of soft-deleted product IDs for this shop.
    # Product.all_objects uses DELETED_VISIBLE policy — no auto-filter is added,
    # so the subquery compiles cleanly without SafeDeleteModel interference.
    # This avoids loading a potentially large ID list into Python memory.
    deleted_products = Product.all_objects.filter(
        deleted__isnull=False,
        shop_service__shop_id=shop_id,
    ).values('id')

    qs = (
        ServiceAttributeValue.objects
        .select_related(
            "product",
            "product__shop_service",
            "product__shop_service__shop",
        )
        .filter(
            attribute__attribute_key='registration_number',
            value=reg_value,
            product__shop_service__shop_id=shop_id,
        )
        .exclude(product_id__in=Subquery(deleted_products))
    )

    # Exclude self during update
    if product is not None:
        qs = qs.exclude(product=product)

    duplicate_attr = qs.first()

    if duplicate_attr:
        raise ValidationError({
            "registration_number": [
                "A product with this registration number already exists in your shop."
            ],
        })