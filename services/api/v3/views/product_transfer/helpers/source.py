from rest_framework.exceptions import NotFound
from services.models import Product, ProductVariant


def _get_source_product_and_variant(self, variant_id):
    """Get source product with related data and its variant."""
    source_product = Product.objects.select_related(
        'shop_service__general_service',
        'shop_service__shop'
    ).filter(
        variants__id=variant_id,
        is_active=True,
        deleted__isnull=True
    ).first()

    if not source_product:
        raise NotFound("Product not found.")

    source_variant = ProductVariant.objects.filter(
        id=variant_id, product=source_product
    ).first()

    if not source_variant:
        raise NotFound("Product variant not found.")

    return source_product, source_variant
