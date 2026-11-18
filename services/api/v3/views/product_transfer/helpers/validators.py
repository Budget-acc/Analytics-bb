from services.models import Product


def _check_product_exists(self, shop_service, source_product):
    """Check if a similar product already exists in the destination service."""
    existing_product = Product.objects.filter(
        shop_service=shop_service,
        name__iexact=source_product.name,
        model__iexact=source_product.model or "",
        category__iexact=source_product.category or "",
        color__iexact=source_product.color or "",
        is_active=True,
        deleted__isnull=True
    ).first()

    if existing_product:
        raise ValueError("A same product already exists in the destination shop.")
