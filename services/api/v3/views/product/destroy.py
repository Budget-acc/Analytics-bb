import logging
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product
from ERP.utils.shop_activity_logs import create_activity_log

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Delete product",
    description="Deletes a product after ownership validation.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Product id.")],
    responses={
        200: OpenApiResponse(description="Exact message: Product deleted successfully."),
        403: OpenApiResponse(description="Exact message: You do not have permission to delete this product."),
        404: OpenApiResponse(description="Exact message: The product you are trying to delete was not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the product. Please try again."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Product deleted successfully.", "data": None}, response_only=True, status_codes=["200"]),
        OpenApiExample("Permission denied", value={"status": "permission_denied", "message": "You do not have permission to delete this product.", "data": None}, response_only=True, status_codes=["403"]),
    ],
)
def destroy(self, request, pk=None):
    try:
        try:
            from shop.api.v3.utils.shop import get_active_shop_id
            shop_id = get_active_shop_id(request)
        except Exception as e:
            return self.permission_denied_response(
                message="You do not have permission to delete this product."
            )

        product = Product.objects.select_related("shop_service__shop").get(pk=pk)

        if product.shop_service.shop.id != shop_id:
            return self.permission_denied_response(
                message="You do not have permission to delete this product."
            )

        product.delete()
        try:
            create_activity_log(
                shop=product.shop_service.shop,
                user=request.user,
                instance=product,
                action="deleted",
                message=f"Product '{product.name}' deleted."
            )
        except Exception:
            logger.exception(f"Failed to create activity log for product delete")

        return self.success_response(
            message="Product deleted successfully.",
            data=None,
        )

    except Product.DoesNotExist:
        return self.not_found_response(
            resource="Product",
            message="The product you are trying to delete was not found."
        )
    except Exception as e:
        logger.error(f"Product delete error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the product. Please try again."
        )
