from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Shop, ShopUser
from django.utils.timezone import localtime
import logging

logger = logging.getLogger("api_requests")

BASE_USER_LIMIT_IN_SHOP = 3


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="List users linked to shop",
    description="Returns all user links for a shop including role and active flag, plus shop user-limit stats.",
    parameters=[OpenApiParameter("shop_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Shop id.")],
    responses={
        200: OpenApiResponse(description="Linked users list response."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Missing 'shop_id' query parameter.\n"
                "- Invalid 'shop_id'. Must be an integer."
            )
        ),
        404: OpenApiResponse(description="Exact message: Shop not found."),
        500: OpenApiResponse(description="Exact message: An error occurred processing your request."),
    },
    examples=[
        OpenApiExample("Success response", value={"users": [{"id": 11, "user_id": 20, "full_name": "Manager One", "role": "MANAGER", "is_active": True}], "user_limit": {"current_active_users": 1, "limit": 3, "base_limit": 3, "extra_limit": 0}, "total_users": 1}, response_only=True, status_codes=["200"]),
    ],
)
def get(self, request):
    """
    GET: List all users linked to a shop.

    Query params:
        - shop_id: Shop ID (required)
    """
    try:
        shop_id = request.query_params.get("shop_id")
        if not shop_id:
            return Response(
                {"error": "Missing 'shop_id' query parameter."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate shop_id is integer
        try:
            shop_id = int(shop_id)
        except (TypeError, ValueError):
            return Response(
                {"error": "Invalid 'shop_id'. Must be an integer."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch shop
        try:
            shop = Shop.objects.get(id=shop_id)
        except Shop.DoesNotExist:
            return Response(
                {"error": "Shop not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Fetch shop users with optimized query
        shop_users = (
            ShopUser.objects
            .filter(shop=shop)
            .select_related("user")
            .order_by("-is_active", "role", "created_at")
        )

        users_data = [
            {
                "id": su.id,
                "user_id": su.user.id,
                "full_name": su.user.full_name,
                "phone": str(su.user.phone),
                "email": su.user.email,
                "role": su.role,
                "is_active": su.is_active,
                "linked_on": localtime(su.created_at).strftime("%d-%m-%Y"),
            }
            for su in shop_users
        ]

        active_count = shop_users.filter(is_active=True).count()
        limit = 3 + shop.extra_user_limit

        return Response(
            {
                "users": users_data,
                "user_limit": {
                    "current_active_users": active_count,
                    "limit": limit,
                    "base_limit": BASE_USER_LIMIT_IN_SHOP,
                    "extra_limit": shop.extra_user_limit
                },
                "total_users": len(users_data)
            },
            status=status.HTTP_200_OK
        )


    except Exception as e:
        logger.exception("Error in ShopUserLinkAPIView.get")
        return Response(
            {"error": "An error occurred processing your request."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
