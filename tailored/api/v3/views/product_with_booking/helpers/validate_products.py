from services.models import ShopOwnerService, Product
from django.db.models.functions import Lower
import logging

logger = logging.getLogger("api_requests")


def _validate_variants_batch(self, variants_data, product_index):
    """Validate a batch of variants for a product"""
    validated_variants = []
    empty_attr_count = 0

    for j, variant in enumerate(variants_data):
        attribute_val = variant.get('attribute', '').strip()
        stock_val = variant.get('stock')
        fabric_length_val = variant.get('fabric_length')
        amount_val = variant.get('amount')
        quantity_val = variant.get('quantity', 1)
        measurements = variant.get('measurements', {})

        # Track empty attributes
        if attribute_val == "":
            empty_attr_count += 1

        # Validate stock
        if stock_val not in [None, "", "null"]:
            try:
                stock_val = int(stock_val)
                if stock_val < 0:
                    return None, f"Variant {j+1} stock must be positive."
            except ValueError:
                return None, f"Variant {j+1} stock must be a number."
        else:
            stock_val = None

        # Validate fabric length
        if fabric_length_val not in [None, "", "null"]:
            try:
                fabric_length_val = float(fabric_length_val)
                if fabric_length_val < 0:
                    return None, f"Variant {j+1} fabric length must be positive."
            except ValueError:
                return None, f"Variant {j+1} fabric length must be a number."
        else:
            fabric_length_val = None

        # Must have at least one of stock or fabric_length
        if stock_val is None and fabric_length_val is None:
            return None, f"Variant '{attribute_val or 'Unnamed'}' must have stock or fabric length."

        # Validate amount
        if amount_val in [None, "", "null"]:
            return None, f"Variant '{attribute_val or 'Unnamed'}' amount is required."

        try:
            amount_val = int(amount_val)
            if amount_val <= 0:
                return None, f"Variant {j+1} amount must be greater than zero."
        except ValueError:
            return None, f"Variant {j+1} amount must be a number."

        # Validate quantity
        try:
            quantity_val = int(quantity_val)
            if quantity_val <= 0:
                return None, f"Variant {j+1} quantity must be greater than zero."
        except ValueError:
            return None, f"Variant {j+1} quantity must be a number."

        validated_variants.append({
            "attribute": attribute_val,
            "stock": stock_val,
            "fabric_length": fabric_length_val,
            "amount": amount_val,
            "quantity": quantity_val,
            "measurements": measurements if measurements else None
        })

    # Validate only one empty attribute allowed per product
    if len(validated_variants) > 1 and empty_attr_count > 0:
        return None, "Only one variant can have an empty attribute per product."

    return validated_variants, None


def _validate_products_batch(self, products_data, shop):
    """Validate all products in batch"""
    validated_products = []
    shop_service_cache = {}

    # Collect all unique shop_service_ids
    unique_service_ids = set()
    for product_data in products_data:
        if product_data['shop_service_id']:
            unique_service_ids.add(product_data['shop_service_id'])

    # Bulk fetch all shop services at once
    if unique_service_ids:
        shop_services = ShopOwnerService.objects.select_related("general_service").filter(
            id__in=unique_service_ids,
            shop=shop
        )
        shop_service_cache = {ss.id: ss for ss in shop_services}

    for i, product_data in enumerate(products_data):
        # Validate shop service
        shop_service_id = product_data['shop_service_id']
        if not shop_service_id:
            return None, f"Product {i+1}: Shop service ID is required."

        if shop_service_id not in shop_service_cache:
            return None, f"Product {i+1}: Selected service is not available."

        shop_service = shop_service_cache[shop_service_id]

        # Validate basic product data
        name = product_data['name']
        if not name:
            return None, f"Product {i+1}: Product name is required."

        try:
            price = int(product_data['price'])
            purchase_price = int(product_data.get('purchase_price', 0))
            if price < 0 or purchase_price < 0:
                return None, f"Product {i+1}: Price must be positive."
        except (ValueError, TypeError):
            return None, f"Product {i+1}: Price must be a valid number."

        # Check product uniqueness
        existing = Product.objects.annotate(
            name_lower=Lower("name"),
            color_lower=Lower("color"),
            model_lower=Lower("model"),
            category_lower=Lower("category")
        ).filter(
            shop_service=shop_service,
            name_lower=name.strip().lower(),
            color_lower=(product_data['color'] or "").strip().lower(),
            model_lower=(product_data['model'] or "").strip().lower(),
            category_lower=(product_data['category'] or "").strip().lower(),
            deleted__isnull=True
        ).exists()

        if existing:
            return None, f"Product {i+1}: A product with same details already exists."

        # Validate variants
        validated_variants, error = _validate_variants_batch(
            self, product_data['variants'], i
        )
        if error:
            return None, f"Product {i+1}: {error}"

        validated_products.append({
            'shop_service': shop_service,
            'name': name,
            'description': product_data['description'],
            'color': product_data['color'],
            'category': product_data['category'],
            'model': product_data['model'],
            'price': price,
            'purchase_price': purchase_price,
            'variants': validated_variants,
            'image_file': product_data['image_file'],
            'image_url': product_data.get('image_url', ''),
            'thumbnail_url': product_data.get('thumbnail_url', ''),
            'tailor_order_item_id': product_data.get('tailor_order_item_id')
        })

    return validated_products, None


def _validate_shop_consistency(self, products_data):
    """
    Ensure all products belong to the same shop (based on shop_service_id).
    Returns: (shop, None) on success or (None, error_response)
    """
    if not products_data:
        return None, self.validation_error_response(
            errors={"products": ["At least one product is required."]},
            message="Products array cannot be empty."
        )

    first_shop = None
    shop_id_set = set()

    for idx, product in enumerate(products_data):
        shop_service_id = product.get("shop_service_id")
        if not shop_service_id:
            return None, self.validation_error_response(
                errors={f"products[{idx}][shop_service_id]": ["This field is required."]},
                message="Missing shop_service_id for one or more products."
            )

        # Validate shop_service
        shop_service = ShopOwnerService.objects.filter(id=shop_service_id).select_related("shop").first()
        if not shop_service:
            return None, self.validation_error_response(
                errors={f"products[{idx}][shop_service_id]": [f"Invalid ID {shop_service_id}."]},
                message=f"Invalid shop_service_id {shop_service_id}."
            )

        shop_id_set.add(shop_service.shop_id)
        if not first_shop:
            first_shop = shop_service.shop

    # Ensure all products are for the same shop
    if len(shop_id_set) > 1:
        return None, self.validation_error_response(
            errors={"shop_service_id": ["All products must belong to the same shop."]},
            message="Multiple shop_service_ids detected in a single booking."
        )

    return first_shop, None


def _get_shop_from_products(self, products_to_create, all_products_data):
    """Extract shop from products data"""
    if not products_to_create:
        return None

    first_product = products_to_create[0]['data']
    shop_service_id = first_product.get('shop_service_id')

    if shop_service_id:
        try:
            shop_service = ShopOwnerService.objects.select_related('shop').get(id=shop_service_id)
            return shop_service.shop
        except ShopOwnerService.DoesNotExist:
            return None


def _validate_shop_service(self, shop_service_id):
    """Validate and get shop service"""
    if not shop_service_id:
        return None, None, None, self.validation_error_response(
            errors={"shop_service_id": ["This field is required."]},
            message="Please select a service for this product."
        )

    try:
        shop_service = ShopOwnerService.objects.select_related("general_service", "shop").get(
            id=shop_service_id
        )
        general_service = shop_service.general_service
        shop = shop_service.shop
        return shop_service, general_service, shop, None
    except ShopOwnerService.DoesNotExist:
        return None, None, None, self.not_found_response(
            resource="Service",
            message="The selected service is not available."
        )
