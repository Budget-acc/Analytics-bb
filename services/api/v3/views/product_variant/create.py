import logging
from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import Product
from services.api.v3.serializers import ProductVariantViewSerializer

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Create product variant",
    description="Creates a new variant under the given product id.",
    parameters=[OpenApiParameter("product_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Parent product id.")],
    request=inline_serializer(
        name="ServiceV3VariantCreateRequest",
        fields={
            "attribute": serializers.CharField(required=True),
            "stock": serializers.IntegerField(required=False),
            "fabric_length": serializers.FloatField(required=False),
            "price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "external_qr_code": serializers.CharField(required=False, allow_blank=True),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Variant created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation messages include:\n"
                "- Product variant already exists\n"
                "- Code already exists.\n"
                "- Please check your variant details and try again.\n"
                "- A variant with this attribute already exists for this product."
            )
        ),
        404: OpenApiResponse(description="Exact message: Product not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating. Please try again."),
    },
    examples=[
        OpenApiExample("Request example", value={"attribute": "M", "stock": 10, "external_qr_code": "QR-123"}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Variant created successfully.", "data": {"id": 21, "attribute": "M", "stock": 10}}, response_only=True, status_codes=["200"]),
    ],
)
def create(self, request, product_id=None):
    try:
        product = Product.objects.select_related("shop_service__shop").get(id=product_id)
    except Product.DoesNotExist:
        return self.not_found_response(
            resource="Product",
            message="Product not found.",
            dev_message=f"Product with id {product_id} not found."
        )

    data = request.data.copy()
    data["product"] = product.id

    serializer = ProductVariantViewSerializer(data=data)
    if serializer.is_valid():
        try:
            with transaction.atomic():
                # Validate external_qr_code uniqueness if present
                code = data.get("external_qr_code")
                if code:
                    self._validate_external_qr_code(code, product.shop_service.shop)
                serializer.save()
            return self.success_response(
                data=serializer.data,
                message="Variant created successfully."
            )
        except ValidationError as ve:
            logger.info(f"[Variant Create] Validation error: {ve}")
            # Extract plain message if ErrorDetail or list
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
            logger.error(f"Variant create error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while creating. Please try again.",
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

    # Flatten non_field_errors manually
    if "non_field_errors" in serializer.errors:
        return self.validation_error_response(
            errors=serializer.errors,
            message="Product variant already exists"
        )

    return self.validation_error_response(
        errors=serializer.errors,
        message="Please check your variant details and try again."
    )
