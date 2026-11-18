from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import ShopUser
from safedelete.models import HARD_DELETE
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Delete shop-user link",
    description="Hard deletes a shop-user relationship by id.",
    parameters=[OpenApiParameter("shop_user_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="ShopUser link id.")],
    responses={
        200: OpenApiResponse(description="Returns deletion confirmation message."),
        400: OpenApiResponse(description="Exact message: 'shop_user_id' parameter is required."),
        404: OpenApiResponse(description="Exact message: Shop-User relationship not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while deleting the user-shop relationship."),
    },
    examples=[
        OpenApiExample("Success response", value={"message": "User 'Manager One' (STAFF) has been permanently removed from shop 'Prime Tailors'.", "deleted_shop_user_id": 56}, response_only=True, status_codes=["200"]),
    ],
)
def delete(self, request, shop_user_id=None):
    """
    Hard delete the shop-user relationship using ShopUser ID via query params
    """
    try:

        if not shop_user_id:
            return Response(
                {"error": "'shop_user_id' parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            shop_user = ShopUser.objects.get(id=shop_user_id)
        except ShopUser.DoesNotExist:
            return Response(
                {"error": "Shop-User relationship not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Store info for response before deletion
        user_name = shop_user.user.full_name or str(shop_user.user.phone)
        shop_name = shop_user.shop.name
        role = shop_user.role

        # Hard delete the relationship
        shop_user.delete(force_policy=HARD_DELETE)
        # logger.error(f"🗑️ ShopUser link (ID: {shop_user_id}) permanently deleted by Admin \n ID: {request.user.id}\n Name: {request.user.full_name} \n User '{user_name}' ({role}) has been permanently removed from shop '{shop_name}'")
        admin_name = request.user.full_name or "N/A"

        logger.error(
            "ShopUser relationship permanently deleted",
            extra={
                "shop_user_id": shop_user_id,
                "admin_id": request.user.id,
                "admin_name": admin_name,
                "removed_user_name": user_name,
                "removed_user_role": role,
                "shop_name": shop_name,
            }
        )

        return Response(
            {
                "message": f"User '{user_name}' ({role}) has been permanently removed from shop '{shop_name}'.",
                "deleted_shop_user_id": shop_user_id,
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        logger.exception("Error in ShopUserLinkAPIView.delete")
        return Response(
            {"error": "An error occurred while deleting the user-shop relationship."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
