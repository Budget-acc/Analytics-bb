import json
import logging

logger = logging.getLogger("api_requests")


def _parse_products_data(self, data, files):
    """Parse multiple products from FormData - now includes image_url and tailor_order_item_id"""
    products_data = []
    i = 0

    while True:
        product_key = f'products[{i}][data]'
        if product_key not in data:
            break

        try:
            product_json = data[product_key]
            product_data = json.loads(product_json)

            # Extract variants
            variants_data = product_data.get('variants', [])
            if not variants_data:
                return None, self.validation_error_response(
                    errors={f"products[{i}]": ["At least one variant is required."]},
                    message=f"Product {i+1} must have at least one variant."
                )

            # Validate variants structure
            for j, variant in enumerate(variants_data):
                if not isinstance(variant, dict):
                    return None, self.validation_error_response(
                        errors={f"products[{i}].variants[{j}]": ["Invalid variant format."]},
                        message=f"Invalid variant format in product {i+1}."
                    )

            # Get image file OR image URL from form data
            image_key = f'products[{i}][image]'
            image_file = files.get(image_key)
            image_url_from_form = data.get(image_key, '')

            # FIX: Properly handle file uploads vs URL strings
            image_url = ""
            if image_file:
                # It's a file upload - process as file
                image_url = ""
                logger.error(f"File upload detected for product {i+1}: {image_file.name}")
            elif image_url_from_form and isinstance(image_url_from_form, str):
                # It's a URL string
                image_url_from_form = image_url_from_form.strip()
                if image_url_from_form.startswith('http'):
                    image_url = image_url_from_form
                    logger.error(f"Image URL detected for product {i+1}: {image_url}")
                    image_file = None  # Don't process as file if it's a URL
                else:
                    # It's a string but not a URL - treat as no image
                    image_url = ""
                    image_file = None
            else:
                # No image provided
                image_url = ""
                image_file = None

            products_data.append({
                'shop_service_id': product_data.get('shop_service_id'),
                'name': product_data.get('name', '').strip(),
                'description': product_data.get('description', ''),
                'color': product_data.get('color', ''),
                'category': product_data.get('category', ''),
                'model': product_data.get('model', ''),
                'price': product_data.get('price', 0),
                'purchase_price': product_data.get('purchase_price', 0),
                'variants': variants_data,
                'image_file': image_file,
                'image_url': image_url,
                'thumbnail_url': product_data.get('thumbnail_url', ''),
                'tailor_order_item_id': product_data.get('tailor_order_item_id')
            })

            i += 1

        except json.JSONDecodeError:
            return None, self.validation_error_response(
                errors={f"products[{i}]": ["Invalid JSON format."]},
                message=f"Invalid product data format for product {i+1}."
            )
        except Exception as e:
            logger.error(f"Error parsing product {i}: {str(e)}")
            return None, self.validation_error_response(
                errors={f"products[{i}]": ["Failed to parse product data."]},
                message=f"Failed to parse product {i+1} data."
            )

    if not products_data:
        return None, self.validation_error_response(
            errors={"products": ["At least one product is required."]},
            message="At least one product is required."
        )

    return products_data, None
