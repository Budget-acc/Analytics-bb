import logging
from django.db.models import F
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product
from services.api.v3.serializers import ProductSerializer
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Retrieve product details",
    description="Returns complete details of a product including service and attribute values.",
    parameters=[
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Product id."),
    ],
    responses={
        200: OpenApiResponse(description="Standard success response with product detail."),
        403: OpenApiResponse(description="Exact message: You do not have permission to access this product."),
        404: OpenApiResponse(description="Exact message: The product you are looking for was not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the product. Please try again."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Product details retrieved successfully.", "data": {"id": 9, "name": "Blue Shirt"}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found", value={"status": "not_found", "message": "The product you are looking for was not found.", "data": None}, response_only=True, status_codes=["404"]),
    ],
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        from shop.api.v3.utils.shop import get_active_shop_id
        shop_id = get_active_shop_id(request)

        product = (
            Product.objects
            .select_related(
                "shop_service__shop",
                "shop_service__general_service__main_category",
            )
            .prefetch_related(
                "attribute_values__attribute"
            )
            .annotate(
                general_service_name=F("shop_service__general_service__name")
            )
            .get(
                pk=pk,
                is_active=True
            )
        )

        # Check if the product belongs to the current shop
        if product.shop_service.shop.id != shop_id:
            return self.permission_denied_response(
                message="You do not have permission to access this product."
            )

        serializer = ProductSerializer(product)
        return self.success_response(
            data=serializer.data,
            message="Product details retrieved successfully."
        )

    except Product.DoesNotExist:
        return self.not_found_response(
            resource="Product",
            message="The product you are looking for was not found."
        )
    except Exception as e:
        logger.error(f"Product retrieve error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the product. Please try again."
        )
