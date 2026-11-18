from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from shop.models import Shop, ShopUser
from authentication.models import User
from shop.api.admin_v1.serializers import ShopUserLinkSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Link existing user to shop",
    description="Creates a shop-user link with role validation and effective shop-user limit checks.",
    request=inline_serializer(
        name="ShopAdminV1LinkUserRequest",
        fields={
            "shop": serializers.IntegerField(required=True),
            "user": serializers.IntegerField(required=True),
            "role": serializers.CharField(required=True, help_text="OWNER | MANAGER | STAFF"),
            "is_active": serializers.BooleanField(required=False),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: User linked to shop successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Missing required fields: 'shop', 'user', and 'role' are required.\n"
                "- Invalid role. Must be one of: OWNER, MANAGER, STAFF\n"
                "- Cannot link users to an inactive shop.\n"
                "- User is already linked to this shop.\n"
                "- Shop user limit exceeded.\n"
                "- Serializer errors returned under errors field."
            )
        ),
        404: OpenApiResponse(description="Exact messages: Shop does not exist. / User does not exist."),
        500: OpenApiResponse(description="Exact message: An error occurred processing your request."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop": 4, "user": 21, "role": "STAFF", "is_active": True}, request_only=True),
        OpenApiExample("Success response", value={"message": "User linked to shop successfully.", "shop_user_id": 56, "user_id": 21, "shop_id": 4, "role": "STAFF", "is_active": True}, response_only=True, status_codes=["201"]),
    ],
)
def post(self, request):
    """
    POST: Link an existing user to a shop with role validation.

    Request body:
        - shop: Shop ID (integer)
        - user: User ID (integer)
        - role: Role string (OWNER/MANAGER/STAFF)
        - is_active: Boolean (optional, defaults to True)
    """
    try:
        shop_id = request.data.get("shop")
        user_id = request.data.get("user")
        role = request.data.get("role")

        # Validate required fields
        if not shop_id or not user_id or not role:
            return Response(
                {"error": "Missing required fields: 'shop', 'user', and 'role' are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate role value
        valid_roles = dict(ShopUser.ROLE_CHOICES)
        if role not in valid_roles:
            return Response(
                {"error": f"Invalid role. Must be one of: {', '.join(valid_roles.keys())}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Use atomic transaction with row-level locking
        with transaction.atomic():
            # Fetch and lock shop to prevent race conditions
            try:
                shop = Shop.objects.select_for_update().get(id=shop_id)
            except Shop.DoesNotExist:
                return Response(
                    {"error": "Shop does not exist."},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Validate shop is active
            if not shop.is_active:
                return Response(
                    {"error": "Cannot link users to an inactive shop."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Fetch user
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                return Response(
                    {"error": "User does not exist."},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Check if user is already linked to this shop
            existing_link = ShopUser.objects.filter(shop=shop, user=user).first()
            if existing_link:
                return Response(
                    {
                        "error": "User is already linked to this shop.",
                        "existing_role": existing_link.role,
                        "is_active": existing_link.is_active
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check shop user limit
            can_add, current_count, limit = self._check_shop_user_limit(shop)

            if not can_add:
                return Response(
                    {
                        "error": "Shop user limit exceeded.",
                        "current_count": current_count,
                        "limit": limit,
                        "base_limit": 3,
                        "extra_limit": shop.extra_user_limit
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )


            # Create the link
            serializer = ShopUserLinkSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(
                    {"errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )

            shop_user = serializer.save()

            return Response(
                {
                    "message": "User linked to shop successfully.",
                    "shop_user_id": shop_user.id,
                    "user_id": shop_user.user.id,
                    "shop_id": shop_user.shop.id,
                    "role": shop_user.role,
                    "is_active": shop_user.is_active,
                },
                status=status.HTTP_201_CREATED
            )

    except Exception as e:
        logger.exception("ShopUserLinkAPIView.post operation failed")
        return Response(
            {"error": "An error occurred processing your request."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
