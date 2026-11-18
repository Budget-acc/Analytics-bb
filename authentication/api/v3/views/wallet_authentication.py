from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from authentication.models import User
from shop.models import Shop, ShopUser
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers


class WalletAuthenticationView(APIView, StandardResponseMixin):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["User Management V3"],
        summary="OTP/Validity-based login (wallet verification)",
        description=(
            "Validates shop wallet access credentials for the authenticated user and active shop context.\n\n"
            "Purpose: Secondary verification step before wallet-sensitive actions.\n\n"
            "Context: Checks user existence, active shop access, and provided wallet password for the shop."
        ),
        request=inline_serializer(
            name="WalletLoginRequestV3",
            fields={
                "password": serializers.CharField(
                    help_text=(
                        "Purpose: Verifies wallet/shop-level credential for secured wallet operations. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="WalletLoginSuccessV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "dev_message": serializers.CharField(),
                    "data": inline_serializer(
                        name="WalletLoginDataV3",
                        fields={
                            "shop_id": serializers.IntegerField(help_text="Active shop ID for wallet context."),
                            "shop_name": serializers.CharField(help_text="Active shop name."),
                            "user_role": serializers.CharField(help_text="Authenticated user's role within the active shop."),
                        },
                    ),
                },
            ),
            400: inline_serializer(
                name="WalletLoginBadRequestV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "dev_message": serializers.CharField(),
                    "errors": serializers.DictField(
                        child=serializers.ListField(child=serializers.CharField()),
                        required=False,
                    ),
                },
            ),
            403: inline_serializer(
                name="WalletLoginForbiddenV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Permission/access failure message."),
                    "dev_message": serializers.CharField(help_text="Developer detail for access denial."),
                },
            ),
            404: inline_serializer(
                name="WalletLoginNotFoundV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "dev_message": serializers.CharField(),
                    "resource": serializers.CharField(required=False),
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Wallet login request",
                value={"password": "123456"},
                request_only=True,
            )
        ],
    )
    def post(self, request):
        # Get current user
        try:
            user = User.objects.get(id=request.user.id)
        except User.DoesNotExist:
            return self.error_response(
                message="User not found",
                dev_message="User not found with the provided ID",
                http_status=status.HTTP_404_NOT_FOUND
            )

        # Get active shop
        try:
            shop_id = get_active_shop_id(request)
        except Exception as e:
            return self.permission_denied_response(
                message="Your access to this shop has been deactivated.",
                dev_message=f"Failed to get active shop ID: {str(e)}"
            )

        # Get shop with optimized query
        try:
            shop = Shop.objects.select_related().get(id=shop_id, is_active=True)
        except Shop.DoesNotExist:
            return self.not_found_response(
                message="Shop not found or inactive",
                dev_message=f"Shop with ID {shop_id} not found or is inactive",
                resource="shop"
            )

        # Validate user's access to this shop
        try:
            shop_user = ShopUser.objects.select_related('user').get(
                user=user,
                shop=shop,
                is_active=True,
                deleted__isnull=True
            )
        except ShopUser.DoesNotExist:
            return self.permission_denied_response(
                message="You don't have access to this shop",
                dev_message=f"User {user.id} not associated with shop {shop_id} or access deactivated"
            )

        # Validate password input
        password = request.data.get("password")
        if not password:
            return self.validation_error_response(
                errors={"password": ["This field is required."]},
                message="Password is required",
                dev_message="Shop password not provided in request"
            )

        # Validate password (plain text comparison for now)
        if shop.password != password:
            return self.error_response(
                message="Invalid password",
                dev_message="Shop password did not match",
                http_status=status.HTTP_400_BAD_REQUEST
            )

        return self.success_response(
            data={
                "shop_id": shop.id,
                "shop_name": shop.name,
                "user_role": shop_user.role
            },
            message="Wallet authentication successful",
            dev_message=f"User {user.id} successfully authenticated for wallet access in shop {shop.id}"
        )
