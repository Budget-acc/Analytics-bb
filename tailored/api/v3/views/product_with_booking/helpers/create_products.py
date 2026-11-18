from django.db import transaction, IntegrityError
from services.models import Product, ProductVariant
import logging

logger = logging.getLogger("api_requests")


def _create_products_bulk(self, validated_products, image_results):
    """Create products and variants in bulk"""
    products = []
    all_variants_data = []
    variant_to_product_map = []

    # Check for any image errors first
    for i, (image_url, thumb_url, error) in enumerate(image_results):
        if error:
            return None, None, error

    # Prepare products for bulk creation
    for i, product_data in enumerate(validated_products):
        image_url, thumb_url, _ = image_results[i]

        # Ensure we have valid image URLs even if no file was uploaded
        if not image_url and product_data['image_file']:
            # This should not happen if image processing worked correctly
            logger.warning(f"Product {i+1}: No image URL generated despite having image file")
            # Continue without image rather than failing
            image_url = ""
            thumb_url = ""

        product = Product(
            shop_service=product_data['shop_service'],
            name=product_data['name'],
            description=product_data['description'],
            color=product_data['color'],
            category=product_data['category'],
            model=product_data['model'],
            price=product_data['price'],
            purchase_price=product_data['purchase_price'],
            image=image_url,
            thumbnail_image=thumb_url
        )
        products.append(product)

    try:
        # Wrap bulk DB writes in a nested savepoint so an IntegrityError only
        # rolls back this inner block and leaves the outer transaction healthy.
        with transaction.atomic():
            # Bulk create products
            created_products = Product.objects.bulk_create(products)

            # Prepare variants for bulk creation
            for i, product in enumerate(created_products):
                product_data = validated_products[i]
                for variant_data in product_data['variants']:
                    variant = ProductVariant(
                        product=product,
                        attribute=variant_data["attribute"],
                        stock=variant_data["stock"] or 0,
                        fabric_length=variant_data["fabric_length"]
                    )
                    all_variants_data.append(variant)
                    variant_to_product_map.append({
                        "product_index": i,
                        "variant_data": variant_data
                    })

            # Bulk create variants
            created_variants = ProductVariant.objects.bulk_create(all_variants_data)

    except IntegrityError as e:
        logger.error(f"Bulk product creation failed: {str(e)}", exc_info=True)
        error_msg = str(e)
        if "unique_product_ci_per_shop" in error_msg:
            error_msg = "A product with the same name, color, model, category, and service already exists in this shop."
        else:
            error_msg = f"Failed to create products: {error_msg}"
        return None, None, error_msg

    # Build response with created variants info
    created_variants_info = []
    for i, variant in enumerate(created_variants):
        mapping = variant_to_product_map[i]
        created_variants_info.append({
            "variant": variant,
            "amount": mapping["variant_data"]["amount"],
            "quantity": mapping["variant_data"]["quantity"],
            "measurements": mapping["variant_data"]["measurements"],
            "product_index": mapping["product_index"]
        })

    return created_products, created_variants_info, None
