import logging
from rest_framework.decorators import action
from django.db.models import Q
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product, ProductVariant
from services.api.v3.serializers import ProductSerializer

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Lookup product by QR/unique code",
    description="Finds an active product by variant external_qr_code or unique_code and validates active shop ownership.",
    parameters=[OpenApiParameter("code", OpenApiTypes.STR, OpenApiParameter.PATH, required=True, description="QR/unique code to scan.")],
    responses={
        200: OpenApiResponse(description="Standard success response with product payload."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Code is required.\n"
                "- Invalid code.\n"
                "- QR code is required.\n"
                "- Invalid QR code length."
            )
        ),
        403: OpenApiResponse(description="Exact message: No product found for the given code."),
        404: OpenApiResponse(description="Exact message: No product found for the given code."),
        500: OpenApiResponse(description="Exact message: Something went wrong while scanning the product. Please try again."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Product retrieved successfully.", "data": {"id": 9, "name": "Blue Shirt"}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found response", value={"status": "not_found", "message": "No product found for the given code.", "data": None}, response_only=True, status_codes=["404"]),
    ],
)
@action(detail=False, methods=["get"], url_path="lookup-externalqr/(?P<code>[^/.]+)")
def lookup_externalqr(self, request, code=None):
    try:
        from shop.api.v3.utils.shop import get_active_shop_id
        shop_id = get_active_shop_id(request)
        # code = request.query_params.get("code", "").strip()

        # ---------- Input validation ----------
        if not code:
            return self.validation_error_response(
                errors={"code": ["QR code is required."]},
                message="Code is required."
            )

        if len(code) > 128:
            return self.validation_error_response(
                errors={"code": ["Invalid QR code length."]},
                message="Invalid code."
            )

        # ---------- Optimized lookup ----------
        variant = (
            ProductVariant.objects
            .select_related(
                "product",
                "product__shop_service",
                "product__shop_service__shop"
            )
            .filter(
                Q(external_qr_code=code) | Q(unique_code=code),
                is_active=True,
                product__is_active=True
            )
            .only(
                "id",
                "product_id",
                "external_qr_code",
                "unique_code"
            )
            .first()
        )

        if not variant:
            return self.not_found_response(
                resource="Product",
                message="No product found for the given code."
            )

        product = variant.product

        # ---------- Shop ownership check ----------
        if product.shop_service.shop_id != shop_id:
            logger.warning(
                f"Unauthorized code scan attempt | shop_id={shop_id} | product_id={product.id}"
            )
            return self.permission_denied_response(
                message="No product found for the given code."
            )

        # ---------- Serialize ----------
        serializer = ProductSerializer(
            product,
            context={"request": request}
        )

        return self.success_response(
            data=serializer.data,
            message="Product retrieved successfully."
        )

    except Exception as e:
        logger.error(
            f"Product scan error | code={request.query_params.get('code')} | error={str(e)}",
            exc_info=True
        )
        return self.error_response(
            message="Something went wrong while scanning the product. Please try again."
        )
