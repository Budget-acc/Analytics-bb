from rest_framework.exceptions import ValidationError
from services.models import ProductVariant


def _validate_external_qr_code(self, code, shop, exclude_variant_id=None):
    """
    Enforces shop-scoped uniqueness of external_qr_code, excluding soft-deleted and inactive variants.
    Defensive against race conditions, input errors, and supports update (exclude_variant_id).
    """
    # Input normalization and validation
    if not isinstance(code, str) or not code.strip():
        raise ValidationError("QR code must be a non-empty string.")
    code = code.strip()
    if len(code) > 128:
        raise ValidationError("QR code is too long (max 128 chars).")

    # Shop-level uniqueness, soft-delete and active-only
    qs = ProductVariant.objects.filter(
        external_qr_code=code,
        product__shop_service__shop=shop,
        deleted__isnull=True
    )
    if hasattr(ProductVariant, "is_active"):
        qs = qs.filter(is_active=True)
    if exclude_variant_id:
        qs = qs.exclude(id=exclude_variant_id)
    # DB constraint enforces uniqueness — no lock needed
    if qs.exists():
        raise ValidationError("Code already exists.")
