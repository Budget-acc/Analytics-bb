from django.db import transaction
from tailored.models import TailorOrderItem
from services.models import Product
from whatsapp_integration.v3.tasks import send_booking_invoice_task
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Convert custom work to products and booking",
    description="Creates one or more products and variants from custom-work items and optionally creates a booking using the created or reused variants.",
    parameters=[
        OpenApiParameter("create_booking", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Defaults to true. Set false to create or reuse products only without creating a booking."),
    ],
    request=inline_serializer(
        name="CustomWorkV3ConvertToBookingRequest",
        fields={
            "products": serializers.ListField(required=True, child=serializers.JSONField(), help_text="Multipart products payload. Each product contains shop_service_id, name, optional description/color/category/model/pricing, variants array, optional image or image URL, optional thumbnail_url, and optional tailor_order_item_id."),
            "client_id": serializers.IntegerField(required=False, allow_null=True),
            "client_name": serializers.CharField(required=False),
            "client_phone_1": serializers.CharField(required=False),
            "phone_1_e164": serializers.CharField(required=False),
            "pickup_date": serializers.CharField(required=False),
            "return_date": serializers.CharField(required=False),
            "cooling_period_end": serializers.CharField(required=False),
            "delivery_status": serializers.CharField(required=False),
            "additional_charges": serializers.CharField(required=False, help_text="JSON string list of additional charge objects."),
            "discount_amount": serializers.IntegerField(required=False),
            "security_amount": serializers.IntegerField(required=False),
            "advance_amount": serializers.IntegerField(required=False),
            "details": serializers.JSONField(required=False),
            "purchase_mode": serializers.CharField(required=False),
            "payment_method": serializers.CharField(required=False),
            "account_id": serializers.IntegerField(required=False, allow_null=True),
            "security_account_id": serializers.IntegerField(required=False, allow_null=True),
            "description": serializers.CharField(required=False),
            "staff_id": serializers.IntegerField(required=False, allow_null=True),
            "send_invoice": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response for either products-only processing or products-plus-booking creation."),
        400: OpenApiResponse(description="Exact error messages include, point by point:\n- At least one variant is required.\n- Product {index} must have at least one variant.\n- Invalid variant format in product {index}.\n- Invalid product data format for product {index}.\n- Failed to parse product {index} data.\n- At least one product is required.\n- Tailor order item {tailor_order_item_id} not found in your shop.\n- Product name mismatch for existing variant.\n- Products array cannot be empty.\n- Missing shop_service_id for one or more products.\n- Invalid shop_service_id {shop_service_id}.\n- Multiple shop_service_ids detected in a single booking.\n- Product limit would be exceeded. Current: {total_existing}, Limit: {effective_limit}, Trying to add: {count}\n- Product {index}: Shop service ID is required.\n- Product {index}: Selected service is not available.\n- Product {index}: Product name is required.\n- Product {index}: Price must be positive.\n- Product {index}: Price must be a valid number.\n- Product {index}: A product with same details already exists.\n- Product {index}: Variant {variant_index} stock must be positive.\n- Product {index}: Variant {variant_index} stock must be a number.\n- Product {index}: Variant {variant_index} fabric length must be positive.\n- Product {index}: Variant {variant_index} fabric length must be a number.\n- Product {index}: Variant '{attribute}' must have stock or fabric length.\n- Product {index}: Variant '{attribute}' amount is required.\n- Product {index}: Variant {variant_index} amount must be greater than zero.\n- Product {index}: Variant {variant_index} amount must be a number.\n- Product {index}: Variant {variant_index} quantity must be greater than zero.\n- Product {index}: Variant {variant_index} quantity must be a number.\n- Product {index}: Only one variant can have an empty attribute per product.\n- Booking already created for this tailor order.\n- Booking already exists for tailor order {shop_tailor_id}.\n- Client name and phone number are required.\n- Pickup date and return date are required.\n- Return time must be after pickup time.\n- Cooling period end must be after return date.\n- Invalid date format.\n- New bookings can't be marked as returned.\n- Can set to 'delivered' only on pickup day.\n- Additional charges must be a list.\n- Invalid additional charges format.\n- Each additional charge must have 'name' and 'amount'.\n- Additional charge '{name}' amount must be positive.\n- Invalid amount for additional charge '{name}'.\n- Discount cannot be negative.\n- Discount amount cannot exceed total amount.\n- Invalid discount amount.\n- Security amount cannot be negative.\n- Invalid security amount.\n- Advance amount must be positive.\n- Advance cannot be more than final total amount.\n- Invalid advance amount.\n- Details must be a valid JSON object.\n- Invalid details format.\n- Invalid account ID.\n- Invalid security account ID.\n- Security account ID is required when security amount is greater than zero.\n- Invalid account selected for payment.\n- Invalid account selected for security payment.\n- Client not found.\n- Staff not found."),
        500: OpenApiResponse(description="Exact error messages include:\n- Failed to create booking: {error}\n- Something went wrong. Please try again.\n- Access to this shop has been deactivated."),
    },
    examples=[
        OpenApiExample("Products and booking request", value={"products": [{"shop_service_id": 12, "name": "Sherwani Rental", "description": "Custom sherwani", "price": 2500, "purchase_price": 1200, "variants": [{"attribute": "42", "stock": 1, "amount": 2500, "quantity": 1}], "tailor_order_item_id": 16}], "client_id": 4, "pickup_date": "2026-05-10T10:00:00", "return_date": "2026-05-12T10:00:00", "advance_amount": 1000, "discount_amount": 100, "security_amount": 500, "security_account_id": 3}, request_only=True),
        OpenApiExample("Products only success", value={"status": "success", "message": "Successfully processed 1 new products and reused 0 existing variants.", "data": {"products_created": 1, "existing_products_used": 0, "total_variants": 1, "product_details": [{"id": 21, "name": "Sherwani Rental", "is_new": True}]}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Products and booking success", value={"status": "success", "message": "Successfully created 1 new products, reused 0 existing variants, and created booking.", "data": {"products_created": 1, "existing_products_used": 0, "total_variants": 1, "booking_id": 44, "shop_booking_id": "BK-0044", "total_amount": 2500, "tailor_orders_updated": ["TO-0008"]}}, response_only=True, status_codes=["200"]),
    ],
)
@transaction.atomic
def create(self, request):
    """
    Create multiple products, variants, and optionally a booking.
    Query parameter: create_booking=true/false (default: true)
    Handles duplicate prevention using TailorOrder fields.
    Each product contains its own tailor_order_item_id for direct mapping.
    """
    savepoint = transaction.savepoint()

    try:
        data = request.data.dict() if hasattr(request.data, 'dict') else request.data
        create_booking = request.query_params.get('create_booking', 'true').lower() == 'true'

        # Step 1: Parse multiple products from FormData
        products_data, error = self._parse_products_data(data, request.FILES)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Step 2: Extract tailor order item IDs and validate
        tailor_order_items_map = {}
        tailor_orders_set = set()

        for product_data in products_data:
            tailor_order_item_id = product_data.get('tailor_order_item_id')
            if tailor_order_item_id:
                try:
                    tailor_order_item = TailorOrderItem.objects.select_related('order').get(
                        id=tailor_order_item_id,
                        order__shop_id=self.shop_id
                    )
                    tailor_order_items_map[tailor_order_item_id] = tailor_order_item
                    tailor_orders_set.add(tailor_order_item.order)

                    # Check if this item already has a created product variant
                    if tailor_order_item.created_product_variant:
                        logger.info(f"TailorOrderItem {tailor_order_item_id} already has product variant {tailor_order_item.created_product_variant_id}")
                except TailorOrderItem.DoesNotExist:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"tailor_order_item_id": [f"Tailor order item {tailor_order_item_id} not found."]},
                        message=f"Tailor order item {tailor_order_item_id} not found in your shop."
                    )

        # Step 3: Separate products into existing and new
        existing_variants_info = []  # Variants that already exist (via created_product_variant)
        products_to_create = []      # Products that need to be created
        product_tailor_item_map = {} # Map product index to tailor order item

        for i, product_data in enumerate(products_data):
            tailor_order_item_id = product_data.get('tailor_order_item_id')

            if tailor_order_item_id:
                tailor_order_item = tailor_order_items_map[tailor_order_item_id]

                # Check if product variant already exists for this tailor item
                if tailor_order_item.created_product_variant:
                    existing_variant = tailor_order_item.created_product_variant

                    # Validate that the existing variant matches the request data
                    if existing_variant.product.name != product_data['name']:
                        transaction.savepoint_rollback(savepoint)
                        return self.validation_error_response(
                            errors={f"products[{i}][name]": ["Product name doesn't match existing variant."]},
                            message=f"Product name mismatch for existing variant."
                        )

                    # Add to existing variants with quantity from tailor item
                    existing_variants_info.append({
                        "variant": existing_variant,
                        "amount": product_data.get('price', 0),  # Use product price as amount
                        "quantity": tailor_order_item.quantity,
                        "measurements": tailor_order_item.measurements,
                        "tailor_order_item": tailor_order_item
                    })
                    continue

            # If no existing variant, add to products to create
            products_to_create.append({
                'index': i,
                'data': product_data,
                'tailor_order_item': tailor_order_items_map.get(tailor_order_item_id) if tailor_order_item_id else None
            })

        # Step 4: Validate shop consistency for new products
        if products_to_create:
            shop, error = self._validate_shop_consistency([p['data'] for p in products_to_create])
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

            # Step 5: Check product limit
            total_existing = Product.objects.filter(
                shop_service__shop=shop,
                deleted__isnull=True
            ).count()

            PLAN_DEFAULT_LIMIT = 150
            effective_limit = PLAN_DEFAULT_LIMIT + shop.extra_stock_limit

            if total_existing + len(products_to_create) > effective_limit:
                transaction.savepoint_rollback(savepoint)
                return self.error_response(
                    message="Product limit is exceeded",
                    dev_message=f"Product limit would be exceeded. Current: {total_existing}, Limit: {effective_limit}, Trying to add: {len(products_to_create)}"
                )

            # Step 6: Validate new products and variants
            validated_products, error = self._validate_products_batch(
                [p['data'] for p in products_to_create], shop
            )
            if error:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"products": [error]},
                    message=error
                )

            # Step 7: Process images for new products
            image_results = self._process_images_parallel(validated_products, shop)

            # Step 8: Create new products and variants
            new_products, new_variants_info, error = self._create_products_bulk(validated_products, image_results)
            if error:
                transaction.savepoint_rollback(savepoint)
                if "already exists" in error:
                    return self.validation_error_response(errors={"products": [error]}, message=error)
                return self.error_response(message=error)

            # Step 9: Link new variants to tailor order items
            for i, product in enumerate(new_products):
                product_data = products_to_create[i]
                tailor_order_item = product_data['tailor_order_item']

                if tailor_order_item:
                    # Update the tailor order item with the created product variant
                    # Use the first variant for this product
                    product_variants = [v for v in new_variants_info if v['product_index'] == i]
                    if product_variants:
                        first_variant = product_variants[0]['variant']
                        tailor_order_item.created_product_variant = first_variant
                        tailor_order_item.save(update_fields=['created_product_variant'])

                        # Add to new variants info with proper mapping
                        for variant_info in product_variants:
                            variant_info['tailor_order_item'] = tailor_order_item
                            existing_variants_info.append(variant_info)
        else:
            new_products = []
            new_variants_info = []

        # Combine all variants for booking creation
        all_variants_info = existing_variants_info

        # If booking creation is not requested, return here
        if not create_booking:
            transaction.savepoint_commit(savepoint)

            # Prepare detailed response
            response_data = {
                "products_created": len(new_products),
                "existing_products_used": len(existing_variants_info),
                "total_variants": len(all_variants_info),
                "product_details": []
            }

            # Add new product details
            for i, product in enumerate(new_products):
                product_variants = [
                    {
                        "id": v["variant"].id,
                        "attribute": v["variant"].attribute,
                        "stock": v["variant"].stock,
                        "fabric_length": v["variant"].fabric_length,
                        "amount": v["amount"],
                        "quantity": v["quantity"]
                    }
                    for v in all_variants_info
                    if any(p['index'] == i for p in products_to_create) and
                    v.get('product_index') == i
                ]

                response_data["product_details"].append({
                    "id": product.id,
                    "name": product.name,
                    "price": product.price,
                    "image": product.image,
                    "thumbnail": product.thumbnail_image,
                    "variants": product_variants,
                    "is_new": True
                })

            # Add existing product details
            for variant_info in existing_variants_info:
                if 'product_index' not in variant_info:  # This is an existing variant
                    variant = variant_info["variant"]
                    response_data["product_details"].append({
                        "id": variant.product.id,
                        "name": variant.product.name,
                        "price": variant.product.price,
                        "image": variant.product.image,
                        "thumbnail": variant.product.thumbnail_image,
                        "variants": [{
                            "id": variant.id,
                            "attribute": variant.attribute,
                            "stock": variant.stock,
                            "fabric_length": variant.fabric_length,
                            "amount": variant_info["amount"],
                            "quantity": variant_info["quantity"]
                        }],
                        "is_new": False
                    })

            return self.success_response(
                data=response_data,
                message=f"Successfully processed {len(new_products)} new products and reused {len(existing_variants_info)} existing variants.",
                dev_message=f"Products-only creation successful. New: {len(new_products)}, Existing: {len(existing_variants_info)}"
            )

        # Step 10: Check if booking already exists for tailor orders
        if tailor_orders_set:
            for tailor_order in tailor_orders_set:
                if tailor_order.created_booking:
                    transaction.savepoint_rollback(savepoint)
                    transaction.set_rollback(True)
                    return self.validation_error_response(
                        errors={"booking": ["Booking already created for this tailor order."]},
                        message=f"Booking already exists for tailor order {tailor_order.shop_tailor_id}."
                    )

        # Step 11: Validate booking data
        booking_data, error = self._validate_booking_data(data, all_variants_info)
        if error:
            transaction.savepoint_rollback(savepoint)
            transaction.set_rollback(True)
            return self.validation_error_response(
                errors={"booking": [error]},
                message=error
            )

        # Step 12: Create booking with all products
        shop = self._get_shop_from_products(products_to_create, products_data) if products_to_create else list(tailor_orders_set)[0].shop
        booking, error = self._create_booking_with_multiple_products(
            request, shop, booking_data, all_variants_info, new_products
        )
        if error:
            transaction.savepoint_rollback(savepoint)
            transaction.set_rollback(True)
            return self.error_response(message=error)

        # Step 13: Link booking back to tailor orders
        if tailor_orders_set:
            for tailor_order in tailor_orders_set:
                tailor_order.created_booking = booking
                # tailor_order.processed_to_booking_sale = True
                tailor_order.save(update_fields=['created_booking'])

        # Commit transaction
        transaction.savepoint_commit(savepoint)

        # Step 14: Send invoice if requested (after commit)
        send_invoice = data.get("send_invoice", False)
        if send_invoice and str(send_invoice).lower() in ['true', '1', 'yes']:
            try:
                send_booking_invoice_task.delay(booking.id)
                logger.info(f"Invoice task queued for booking {booking.id}")
            except Exception as e:
                logger.error(f"Failed to queue invoice task: {str(e)}")

        return self.success_response(
            data={
                "products_created": len(new_products),
                "existing_products_used": len(existing_variants_info),
                "total_variants": len(all_variants_info),
                "booking_id": booking.id,
                "shop_booking_id": booking.shop_booking_id,
                "total_amount": booking_data["total_amount"],
                "tailor_orders_updated": [order.shop_tailor_id for order in tailor_orders_set]
            },
            message=f"Successfully created {len(new_products)} new products, reused {len(existing_variants_info)} existing variants, and created booking."
        )

    except Exception as e:
        try:
            transaction.savepoint_rollback(savepoint)
            transaction.set_rollback(True)
        except Exception:
            pass
        logger.error(f"Multi-product booking creation error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong. Please try again.",
            dev_message=f"Unexpected error: {str(e)}"
        )
