import logging
from rest_framework.decorators import action
from django.db import transaction
from django.db.utils import IntegrityError
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import Product, ProductVariant, ProductTransferHistory

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Transfer stock between shops",
    description="Transfers stock from source variant to destination shop product (existing or cloned) with history logging.",
    request=inline_serializer(
        name="ServiceV3TransferStockRequest",
        fields={
            "from_variant_id": serializers.IntegerField(required=True),
            "to_shop_id": serializers.IntegerField(required=True),
            "transfer_quantity": serializers.IntegerField(required=True),
            "to_product_id": serializers.IntegerField(required=False, help_text="Optional existing destination product id."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Transfer success response with transferred ids and quantity."),
        400: OpenApiResponse(
            description=(
                "Validation messages include:\n"
                "- Please provide all required fields.\n"
                "- Quantity must be a positive integer.\n"
                "- Please enter a valid transfer quantity.\n"
                "- Only {source_variant.stock} items available in stock."
            )
        ),
        403: OpenApiResponse(description="Exact message: You do not have permission to transfer stock between these shops."),
        404: OpenApiResponse(description="Exact message: Destination product not found."),
        500: OpenApiResponse(
            description=(
                "Error messages include:\n"
                "- The destination shop service is deactivated. Please try again.\n"
                "- Something went wrong while transferring stock."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"from_variant_id": 22, "to_shop_id": 5, "transfer_quantity": 3, "to_product_id": 33}, request_only=True),
        OpenApiExample("Success to existing response", value={"status": "success", "message": "Stock transferred successfully.", "data": {"to_product_id": 33, "to_variant_id": 44, "transferred_quantity": 3}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Success cloned response", value={"status": "success", "message": "The product and its stock have been successfully moved.", "data": {"new_product_id": 72, "new_variant_id": 101, "transferred_quantity": 3}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["post"], url_path="transfer-stock")
@transaction.atomic
def transfer_stock(self, request):
    try:
        user = request.user
        from_variant_id = request.data.get("from_variant_id")
        to_shop_id = request.data.get("to_shop_id")
        transfer_quantity_str = request.data.get("transfer_quantity")
        to_product_id = request.data.get("to_product_id", None)

        # Get source product and variant
        source_product, source_variant = self._get_source_product_and_variant(from_variant_id)

        source_shop_id = source_product.shop_service.shop.id
        if not self._check_user_permission(request.user, [source_shop_id, to_shop_id]):
            return self.permission_denied_response(
                message="You do not have permission to transfer stock between these shops."
            )

        # Validate required fields
        required_fields = [from_variant_id, to_shop_id, transfer_quantity_str]
        if any(field is None for field in required_fields):
            return self.validation_error_response(
                errors={"fields": ["Missing required fields."]},
                message="Please provide all required fields."
            )

        # Validate transfer quantity
        try:
            transfer_quantity = int(transfer_quantity_str)
            if transfer_quantity <= 0:
                return self.validation_error_response(
                    errors={"transfer_quantity": ["Transfer quantity must be a positive integer."]},
                    message="Quantity must be a positive integer."
                )
        except (ValueError, TypeError):
            return self.validation_error_response(
                errors={"transfer_quantity": ["Invalid transfer quantity. It must be a number."]},
                message="Please enter a valid transfer quantity."
            )

        # Basic stock validation
        if source_variant.stock < transfer_quantity:
            return self.validation_error_response(
                errors={"stock": [f"Only {source_variant.stock} items available in stock."]},
                message=f"Only {source_variant.stock} items available in stock."
            )

        # Get destination service (create if needed)
        destination_service = self._get_destination_service(
            to_shop_id, source_product.shop_service.general_service, user, create_if_missing=True
        )

        # Check available stock considering bookings
        # available_stock = self._calculate_available_stock(source_variant)

        # if available_stock <= 0:
        #     return self.error_response(
        #         message="Stock transfer not allowed. All units are booked.",
        #         dev_message="All units are booked for this variant."
        #     )

        # if transfer_quantity > available_stock:
        #     return self.validation_error_response(
        #         errors={"stock": [f"Only {available_stock} units are available for transfer due to existing bookings."]},
        #         message=f"Only {available_stock} units in stock for transfer."
        #     )

        # Create product snapshot for history
        product_snapshot = {
            "name": source_product.name,
            "model": source_product.model,
            "category": source_product.category,
            "color": source_product.color,
            "price": source_product.price,
            "variant_attribute": source_variant.attribute,
            "original_stock": source_variant.stock
        }

        destination_product = None
        dest_variant = None
        transfer_type = "CREATE_NEW"

        # Transfer to existing product if specified
        if to_product_id:
            destination_product = Product.objects.filter(
                id=to_product_id, shop_service=destination_service, is_active=True
            ).first()

            if not destination_product:
                return self.not_found_response(
                    resource="Product",
                    message="Destination product not found.",
                    dev_message=f"Destination product {to_product_id} not found."
                )

            transfer_type = "TO_EXISTING"

            # Find or create matching variant
            dest_variant = ProductVariant.all_objects.filter(
                product=destination_product, attribute=source_variant.attribute
            ).first()

            if dest_variant:
                if dest_variant.deleted:
                    dest_variant.undelete()
                    dest_variant.stock = 0
                dest_variant.stock += transfer_quantity
                dest_variant.save()
            else:
                dest_variant = ProductVariant.objects.create(
                    product=destination_product,
                    attribute=source_variant.attribute,
                    stock=transfer_quantity
                )

            # Reduce source stock
            source_variant.stock -= transfer_quantity
            source_variant.save()

            # Create transfer history record
            ProductTransferHistory.objects.create(
                source_shop=source_product.shop_service.shop,
                source_product=source_product,
                source_variant=source_variant,
                destination_shop=destination_service.shop,
                destination_product=destination_product,
                destination_variant=dest_variant,
                quantity_transferred=transfer_quantity,
                transfer_type=transfer_type,
                initiated_by=user,
                product_snapshot=product_snapshot,
                status="COMPLETED"
            )

            return self.success_response(
                data={
                    "to_product_id": destination_product.id,
                    "to_variant_id": dest_variant.id,
                    "transferred_quantity": transfer_quantity
                },
                message="Stock transferred successfully."
            )

        # Check if similar product already exists before creating new one
        self._check_product_exists(destination_service, source_product)

        # Create new product if no matching product specified
        cloned_product = Product.objects.create(
            name=source_product.name,
            model=source_product.model,
            category=source_product.category,
            color=source_product.color,
            price=source_product.price,
            purchase_price=source_product.purchase_price,
            image=source_product.image,
            thumbnail_image=source_product.thumbnail_image,
            shop_service=destination_service,
            is_active=True
        )

        cloned_variant = ProductVariant.objects.create(
            product=cloned_product,
            attribute=source_variant.attribute,
            stock=transfer_quantity
        )

        # Reduce source stock
        source_variant.stock -= transfer_quantity
        source_variant.save()

        ProductTransferHistory.objects.create(
            source_shop=source_product.shop_service.shop,
            source_product=source_product,
            source_variant=source_variant,
            destination_shop=destination_service.shop,
            destination_product=cloned_product,
            destination_variant=cloned_variant,
            quantity_transferred=transfer_quantity,
            transfer_type=transfer_type,
            initiated_by=user,
            product_snapshot=product_snapshot,
            status="COMPLETED"
        )

        return self.success_response(
            data={
                "new_product_id": cloned_product.id,
                "new_variant_id": cloned_variant.id,
                "transferred_quantity": transfer_quantity
            },
            message="The product and its stock have been successfully moved."
        )

    except ValueError as ve:
        return self.error_response(
            message=str(ve)
        )
    except IntegrityError as ie:
        logger.error(f"Transfer stock integrity error: {str(ie)}", exc_info=True)
        return self.error_response(
            message="The destination shop service is deactivated. Please try again.",
            dev_message=str(ie)
        )
    except Exception as e:
        logger.error(f"Transfer stock error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while transferring stock.",
            dev_message=str(e)
        )
