from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from shop.models import ShopUser
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Update linked shop user",
    description="Updates role and/or active status for a shop-user link, enforcing role validation and activation limit checks.",
    parameters=[OpenApiParameter("shop_user_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="ShopUser link id.")],
    request=inline_serializer(
        name="ShopAdminV1PatchLinkUserRequest",
        fields={
            "role": serializers.CharField(required=False, help_text="OWNER | MANAGER | STAFF"),
            "is_active": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: ShopUser updated successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Missing 'shop_user_id' path parameter.\n"
                "- Invalid role. Must be one of: OWNER, MANAGER, STAFF\n"
                "- Shop user limit exceeded. Cannot activate user."
            )
        ),
        404: OpenApiResponse(description="Exact message: ShopUser not found."),
        500: OpenApiResponse(description="Exact message: An error occurred processing your request."),
    },
    examples=[
        OpenApiExample("Request example", value={"role": "MANAGER", "is_active": True}, request_only=True),
        OpenApiExample("Success response", value={"id": 56, "message": "ShopUser updated successfully.", "user_id": 21, "shop_id": 4, "role": "MANAGER", "is_active": True, "changes": {"role_changed": True, "active_status_changed": False}}, response_only=True, status_codes=["200"]),
    ],
)
def patch(self, request, shop_user_id=None):
    """
    PATCH: Update shop user's role or active status with role limit validation.

    Path params:
        - shop_user_id: ShopUser ID (required)

    Request body:
        - role: New role (optional)
        - is_active: New active status (optional)
    """
    try:
        if not shop_user_id:
            return Response(
                {"error": "Missing 'shop_user_id' path parameter."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Use atomic transaction with row-level locking
        with transaction.atomic():
            # Fetch and lock the shop user record
            try:
                shop_user = (
                    ShopUser.objects
                    .select_for_update()
                    .select_related("shop", "user")
                    .get(id=shop_user_id)
                )
            except ShopUser.DoesNotExist:
                return Response(
                    {"error": "ShopUser not found."},
                    status=status.HTTP_404_NOT_FOUND
                )

            old_role = shop_user.role
            old_is_active = shop_user.is_active

            role = request.data.get("role")
            is_active = request.data.get("is_active")

            # Validate role if provided
            if role:
                valid_roles = dict(ShopUser.ROLE_CHOICES)
                if role not in valid_roles:
                    return Response(
                        {"error": f"Invalid role. Must be one of: {', '.join(valid_roles.keys())}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                shop_user.role = role

            # Update is_active if provided
            if is_active is not None:
                new_is_active = is_active in [True, "true", "True", 1, "1"]

                if new_is_active and not old_is_active:
                    can_activate, current_count, limit = self._check_shop_user_limit(
                        shop_user.shop,
                        exclude_shop_user_id=shop_user.id
                    )

                    if not can_activate:
                        return Response(
                            {
                                "error": "Shop user limit exceeded. Cannot activate user.",
                                "current_count": current_count,
                                "limit": limit
                            },
                            status=status.HTTP_400_BAD_REQUEST
                        )


                shop_user.is_active = new_is_active

            # Save changes
            shop_user.save()

            return Response(
                {
                    "id": shop_user.id,
                    "message": "ShopUser updated successfully.",
                    "user_id": shop_user.user.id,
                    "shop_id": shop_user.shop.id,
                    "role": shop_user.role,
                    "is_active": shop_user.is_active,
                    "changes": {
                        "role_changed": old_role != shop_user.role,
                        "active_status_changed": old_is_active != shop_user.is_active
                    }
                },
                status=status.HTTP_200_OK,
            )

    except Exception as e:
        logger.exception("Error in ShopUserLinkAPIView.patch")
        return Response(
            {"error": "An error occurred processing your request."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
