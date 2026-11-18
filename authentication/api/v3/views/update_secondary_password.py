from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from authentication.models import User
from shop.models import Shop, ShopUser
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from django.db import transaction
from django.utils import timezone
import logging
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers

logger = logging.getLogger(__name__)


class UpdateSecondaryPasswordView(APIView, StandardResponseMixin):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["User Management V3"],
        summary="Secondary password update",
        description=(
            "Updates the active shop wallet/secondary password.\n\n"
            "Purpose: Allows authorized shop owners to rotate the shop's secondary credential used for wallet checks.\n\n"
            "Context: Requires active shop access, OWNER role, current secondary password validation, "
            "and password policy checks (length/whitespace/difference from current)."
        ),
        request=inline_serializer(
            name="UpdateSecondaryPasswordRequestV3",
            fields={
                "old_secondary_password": serializers.CharField(
                    help_text=(
                        "Purpose: Verifies current shop secondary password before update. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
                "new_secondary_password": serializers.CharField(
                    help_text=(
                        "Purpose: Sets the new shop secondary password for future wallet authentication. "
                        "Location: Request body. "
                        "Format/Type: string; constraints - length 6 to 50 characters, no spaces, must differ from current password. "
                        "Required."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="UpdateSecondaryPasswordSuccessV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "dev_message": serializers.CharField(),
                    "data": inline_serializer(
                        name="UpdateSecondaryPasswordDataV3",
                        fields={
                            "shop_id": serializers.IntegerField(),
                            "shop_name": serializers.CharField(),
                            "password_changed_at": serializers.CharField(help_text="ISO-8601 timestamp of password change."),
                        },
                    ),
                },
            ),
            400: inline_serializer(
                name="UpdateSecondaryPasswordBadRequestV3",
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
                name="UpdateSecondaryPasswordForbiddenV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "dev_message": serializers.CharField(),
                },
            ),
            404: inline_serializer(
                name="UpdateSecondaryPasswordNotFoundV3",
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
                "Update secondary password request",
                value={
                    "old_secondary_password": "123456",
                    "new_secondary_password": "654321",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        # Get current user
        try:
            user = User.objects.get(id=request.user.id)
        except User.DoesNotExist:
            return self.not_found_response(
                message="User not found",
                dev_message=f"User with ID {request.user.id} not found",
                resource="user"
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

        # Only OWNER can update shop password
        if shop_user.role != "OWNER":
            return self.permission_denied_response(
                message="Only shop owners can update the shop password",
                dev_message=f"User {user.id} has role {shop_user.role}, but OWNER role required"
            )

        # Validate input fields
        current_password = request.data.get("old_secondary_password")
        new_password = request.data.get("new_secondary_password")

        # Check for required fields
        missing_fields = {}
        if not current_password:
            missing_fields["current_password"] = ["This field is required."]
        if not new_password:
            missing_fields["new_password"] = ["This field is required."]

        if missing_fields:
            return self.validation_error_response(
                errors=missing_fields,
                message="Required fields are missing",
                dev_message=f"Missing required fields: {', '.join(missing_fields.keys())}"
            )

        # Validate current password
        if shop.password != current_password:
            return self.error_response(
                message="Current password is incorrect",
                dev_message="Provided current password doesn't match shop's current password",
            )

        # Validate password requirements
        validation_errors = self._validate_password_requirements(new_password)
        if validation_errors:
            return self.validation_error_response(
                errors={"new_password": validation_errors},
                message="Password does not meet requirements",
                dev_message="Password validation failed"
            )

        # Prevent setting same password
        if current_password == new_password:
            return self.validation_error_response(
                errors={"new_password": ["New password must be different from current password."]},
                message="New password must be different from current password",
                dev_message="Attempted to set same password as current"
            )

        # Update shop password
        try:
            with transaction.atomic():
                shop.password = new_password
                shop.password_changed_at = timezone.now()
                shop.save(update_fields=['password', 'password_changed_at'])

                # Log successful password change
                logger.info(f"Shop password updated successfully for shop {shop.id} by user {user.id}")

                return self.success_response(
                    data={
                        "shop_id": shop.id,
                        "shop_name": shop.name,
                        "password_changed_at": shop.password_changed_at.isoformat()
                    },
                    message="Shop password updated successfully",
                    dev_message=f"Shop {shop.id} password updated by user {user.id} at {shop.password_changed_at}"
                )

        except Exception as e:
            logger.error(f"Failed to update shop password for shop {shop.id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to update shop password",
                dev_message=str(e)
            )

    def _validate_password_requirements(self, password):
        """
        Validate password meets basic security requirements
        Returns list of error messages if validation fails
        """
        errors = []

        # Minimum length check
        if len(password) < 6:
            errors.append("Password must be at least 6 characters long.")

        # Maximum length check
        if len(password) > 50:
            errors.append("Password must be no more than 50 characters long.")

        # Check for whitespace
        if ' ' in password:
            errors.append("Password cannot contain spaces.")

        return errors
