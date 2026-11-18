from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import action
from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from shop.models import Shop, ShopUser
from shop.api.admin_v1.serializers import UserSerializer
from authentication.models import User
from ERP.utils.custom_pagination import StandardPageNumberPagination
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")

BASE_USER_LIMIT_IN_SHOP = 3


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Create user and assign to shop role",
    description="Creates a user and links to shop with role validation and effective user-limit checks.",
    request=inline_serializer(
        name="ShopAdminV1CreateUserWithRoleRequest",
        fields={
            "shop_id": serializers.IntegerField(required=True),
            "shop_role": serializers.CharField(required=True, help_text="OWNER | MANAGER | STAFF"),
            "full_name": serializers.CharField(required=True),
            "phone": serializers.CharField(required=True),
            "password": serializers.CharField(required=True),
            "email": serializers.EmailField(required=False, allow_blank=True),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: User created and assigned to shop successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Both 'shop_id' and 'shop_role' are required.\n"
                "- Invalid shop_id. Must be a positive integer.\n"
                "- Invalid shop_role. Must be a string.\n"
                "- Invalid shop_role. Must be one of: OWNER, MANAGER, STAFF\n"
                "- User with phone number {phone} already exists in the system.\n"
                "- Shop not found.\n"
                "- Cannot assign users to an inactive shop.\n"
                "- Shop user limit exceeded. Current: {current_count}, Allowed: {effective_limit} (Base: 3, Extra: {extra_user_limit})\n"
                "- UserSerializer validation errors under errors field."
            )
        ),
        404: OpenApiResponse(description="Exact message: Shop not found."),
        429: OpenApiResponse(description="Throttle message from DRF rate limiting (10/hour)."),
        500: OpenApiResponse(description="Exact message: Failed to create user and assign to shop. Please try again."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_id": 4, "shop_role": "MANAGER", "full_name": "Manager One", "phone": "8888888888", "password": "Secret@123"}, request_only=True),
        OpenApiExample("Success response", value={"message": "User created and assigned to shop successfully.", "user": {"id": 21, "phone": "8888888888"}, "shop_user_id": 55, "user_limits": {"current_count": 2, "limit": 3, "base_limit": 3, "extra_limit": 0}}, response_only=True, status_codes=["201"]),
    ],
)
@action(detail=False, methods=["post"], url_path="create-with-role")
def create_user_with_shop_assignment(self, request):
    """
    Create a new user and assign them to a shop with role validation.

    Features:
    - Rate limiting (10 requests per hour)
    - Race condition protection via shop locking
    - Duplicate user prevention
    - Atomic transaction handling
    - Comprehensive validation

    Rate Limit: 10/hour per user
    Request body:
        - shop_id: Shop ID (integer)
        - shop_role: Role (OWNER/MANAGER/STAFF)
        - [user fields]: phone, full_name, email, etc.
    """
    # Apply rate limiting
    from rest_framework.throttling import UserRateThrottle

    class CreateUserThrottle(UserRateThrottle):
        scope = 'create_user'

    self.throttle_classes = [CreateUserThrottle]
    self.check_throttles(request)
    # ==================== 1: INPUT VALIDATION ====================
    shop_id = request.data.get("shop_id")
    shop_role = request.data.get("shop_role")

    # Validate required fields presence
    if not shop_id or not shop_role:
        return Response(
            {"error": "Both 'shop_id' and 'shop_role' are required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Validate shop_id is integer
    try:
        shop_id = int(shop_id)
        if shop_id <= 0:
            raise ValueError("Shop ID must be positive")
    except (TypeError, ValueError):
        return Response(
            {"error": "Invalid shop_id. Must be a positive integer."},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Validate shop_role is string
    if not isinstance(shop_role, str):
        return Response(
            {"error": "Invalid shop_role. Must be a string."},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Normalize role to uppercase
    shop_role = shop_role.strip().upper()

    # Validate shop_role value
    valid_roles = dict(ShopUser.ROLE_CHOICES)
    if shop_role not in valid_roles:
        return Response(
            {"error": f"Invalid shop_role. Must be one of: {', '.join(valid_roles.keys())}"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # ==================== 2: USER DATA VALIDATION ====================
    user_serializer = UserSerializer(data=request.data)
    if not user_serializer.is_valid():
        return Response(
            {"errors": user_serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )

    # ==================== 3: DUPLICATE USER CHECK ====================
    # Extract phone from validated data (phone is unique per system)
    phone = user_serializer.validated_data.get("phone")

    # Check if user with this phone already exists (prevents duplicate users)
    if User.objects.filter(phone=phone).exists():
        return Response(
            {
                "error": f"User with phone number {phone} already exists in the system.",
                "phone": str(phone)
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    # ==================== 4: ATOMIC TRANSACTION ====================
    # Initialize variables for response building
    shop = None
    user = None
    shop_user = None
    current_count = 0
    limit = 0

    try:
        with transaction.atomic():
            # ==================== 4a: LOCK SHOP ====================
            # Lock shop before counting
            try:
                shop = Shop.objects.select_for_update().get(id=shop_id)
            except Shop.DoesNotExist:
                raise DjangoValidationError("Shop not found.")

            # ==================== 4b: VALIDATE SHOP ====================
            if not shop.is_active:
                raise DjangoValidationError("Cannot assign users to an inactive shop.")

            # ==================== 4c: SHOP USER LIMIT CHECK (AFTER LOCK) ====================
            effective_limit = BASE_USER_LIMIT_IN_SHOP + shop.extra_user_limit

            current_count = ShopUser.objects.filter(
                shop=shop,
                is_active=True
            ).count()

            if current_count >= effective_limit:
                raise DjangoValidationError(
                    f"Shop user limit exceeded. "
                    f"Current: {current_count}, "
                    f"Allowed: {effective_limit} "
                    f"(Base: {BASE_USER_LIMIT_IN_SHOP}, Extra: {shop.extra_user_limit})"
                )


            # ==================== 5: USER CREATION ====================
            user = user_serializer.save()

            # ==================== 6: SHOP USER LINK CREATION ====================
            shop_user = ShopUser.objects.create(
                shop=shop,
                user=user,
                role=shop_role,
                is_active=True
            )

        # ====================  7: LOGGING (OUTSIDE TRANSACTION) ====================
        # Logging removed for privacy compliance - PII should not be logged

    # ==================== ERROR HANDLING ====================
    except DjangoValidationError as ve:
        # Extract message from DjangoValidationError (can be string or list)
        error_message = ve.message if hasattr(ve, 'message') else str(ve)
        if isinstance(error_message, str) and error_message.startswith('['):
            # Handle case where str(ve) returns list-like string
            try:
                error_message = ve.messages[0] if ve.messages else str(ve)
            except (AttributeError, IndexError):
                error_message = str(ve)

        # Validation errors (caught during transaction, transaction automatically rolled back)
        logger.warning(
            f"[User Creation] Validation failed for shop {shop_id}: {error_message}",
            extra={
                "shop_id": shop_id,
                "role": shop_role,
                "phone": phone if phone else "N/A"
            }
        )
        return Response(
            {"error": error_message},
            status=status.HTTP_400_BAD_REQUEST
        )

    except Shop.DoesNotExist:
        # Shop doesn't exist (shouldn't reach here due to DjangoValidationError, but catch-all)
        logger.warning(f"[User Creation] Shop {shop_id} not found")
        return Response(
            {"error": "Shop not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    except Exception as e:
        # Transaction automatically rolled back
        logger.error(
            f"[User Creation] Unexpected error for shop {shop_id}: {str(e)}",
            exc_info=True,
            extra={
                "shop_id": shop_id,
                "role": shop_role,
                "error_type": type(e).__name__
            }
        )
        return Response(
            {"error": "Failed to create user and assign to shop. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # ==================== 8: SUCCESS RESPONSE ====================
    return Response(
        {
            "message": "User created and assigned to shop successfully.",
            "user": UserSerializer(user).data,
            "shop_user_id": shop_user.id,
            "user_limits": {
                "current_count": current_count + 1,
                "limit": effective_limit,
                "base_limit": 3,
                "extra_limit": shop.extra_user_limit
            }
        },
        status=status.HTTP_201_CREATED
    )
