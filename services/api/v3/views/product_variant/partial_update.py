import logging
from django.db import IntegrityError, transaction
from django.utils.timezone import now
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import ProductVariant
from booking_v2.models import ProductVariantAvailability
from services.api.v3.serializers import ProductVariantViewSerializer

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Update product variant",
    description="Partially updates a variant with stock/fabric validations and QR uniqueness checks.",
    parameters=[
        OpenApiParameter("product_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Parent product id."),
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Variant id."),
    ],
    request=inline_serializer(
        name="ServiceV3VariantPatchRequest",
        fields={
            "attribute": serializers.CharField(required=False),
            "stock": serializers.IntegerField(required=False),
            "fabric_length": serializers.FloatField(required=False),
            "price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "external_qr_code": serializers.CharField(required=False, allow_blank=True),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Variant updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation messages include:\n"
                "- Invalid stock value.\n"
                "- Keep at least {max_booked_quantity} items for current bookings.\n"
                "- Fabric length must be a positive number.\n"
                "- Invalid length.\n"
                "- Code already exists.\n"
                "- Product variant already exists.\n"
                "- Please check your variant details and try again."
            )
        ),
        404: OpenApiResponse(description="Exact message: Variant not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating. Please try again."),
    },
    examples=[
        OpenApiExample("Request example", value={"stock": 8, "fabric_length": 12.5}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Variant updated successfully.", "data": {"id": 21, "stock": 8}}, response_only=True, status_codes=["200"]),
    ],
)
def partial_update(self, request, pk=None, product_id=None):
    try:
        variant = ProductVariant.objects.select_related("product__shop_service__shop").get(pk=pk, product_id=product_id)
    except ProductVariant.DoesNotExist:
        return self.not_found_response(
            resource="ProductVariant",
            message="Variant not found.",
            dev_message=f"Variant with id {pk} for product {product_id} not found."
        )

    new_stock = request.data.get("stock", None)
    if new_stock is not None:
        try:
            new_stock = int(new_stock)
        except ValueError:
            return self.validation_error_response(
                errors={"stock": ["Invalid stock value."]},
                message="Invalid stock value."
            )

        today = now().date()

        daily_bookings = ProductVariantAvailability.objects.filter(
            variant=variant,
            date__gte=today,
        ).values_list("booked_quantity", flat=True)

        max_booked_quantity = max(daily_bookings, default=0)
        if new_stock < max_booked_quantity:
            return self.validation_error_response(
                errors={"stock": [f"You can't reduce the stock below {max_booked_quantity} because you have existing bookings for this item."]},
                message=f"Keep at least {max_booked_quantity} items for current bookings."
            )

    new_fabric_length = request.data.get("fabric_length", None)
    if new_fabric_length is not None:
        try:
            new_fabric_length = float(new_fabric_length)
            if new_fabric_length < 0:
                raise ValueError
        except (ValueError, TypeError):
            return self.validation_error_response(
                errors={"fabric_length": ["Fabric length must be a positive number."]},
                message="Invalid length."
            )
        request.data["fabric_length"] = new_fabric_length

    serializer = ProductVariantViewSerializer(variant, data=request.data, partial=True)
    if serializer.is_valid():
        try:
            with transaction.atomic():
                code = request.data.get("external_qr_code")
                if code:
                    self._validate_external_qr_code(code, variant.product.shop_service.shop, exclude_variant_id=variant.id)
                serializer.save()
            return self.success_response(
                data=serializer.data,
                message="Variant updated successfully."
            )
        except ValidationError as ve:
            logger.info(f"[Variant Update] Validation error: {ve}")
            msg = str(ve)
            if hasattr(ve, 'detail'):
                detail = ve.detail
                if isinstance(detail, (list, tuple)) and detail:
                    msg = str(detail[0])
                elif isinstance(detail, dict) and detail:
                    msg = str(next(iter(detail.values()))[0])
                elif isinstance(detail, str):
                    msg = detail
            return self.validation_error_response(
                errors={"external_qr_code": [msg]},
                message=msg
            )
        except IntegrityError as ie:
            if 'unique_external_qr_per_shop' in str(ie):
                return self.validation_error_response(
                    errors={"external_qr_code": ["Code already exists."]},
                    message="Code already exists."
                )
            return self.validation_error_response(
                errors={"non_field_errors": ["A variant with this attribute already exists for this product."]},
                message="Product variant already exists"
            )
        except Exception as e:
            logger.error(f"Variant update error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while updating. Please try again.",
                dev_message=str(e)
            )

    # --- Custom error message for duplicate attribute ---
    if (
        "attribute" in serializer.errors and
        "already exists" in str(serializer.errors["attribute"][0])
    ):
        return self.validation_error_response(
            errors=serializer.errors,
            message="Product variant already exists"
        )

    # Flatten non_field_errors
    if "non_field_errors" in serializer.errors:
        return self.validation_error_response(
            errors=serializer.errors,
            message="Product variant already exists."
        )

    return self.validation_error_response(
        errors=serializer.errors,
        message="Please check your variant details and try again."
    )
