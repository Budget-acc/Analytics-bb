from rest_framework import status
from rest_framework.decorators import action
from rest_framework import serializers
from authentication.models import User
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from authentication.permissions import IsAdminPanelUser


@extend_schema(
    tags=["Authentication Admin v1"],
    summary="Change own password (admin)",
    description=(
        "Allows an authenticated admin-panel user (ADMIN or VENDOR_ADMIN) to change their own account password.\n\n"
        "Purpose: Provides a self-service mechanism for admins to rotate their password without "
        "requiring intervention from a superuser.\n\n"
        "Context: Use after the admin is logged in and has a valid JWT access token. "
        "The old password is verified before the new one is applied, preventing unauthorized changes "
        "if the token is somehow compromised. The password_changed_at timestamp is updated, which "
        "invalidates all previously issued tokens for that account.\n\n"
        "Authentication: JWT Bearer token required in the Authorization header.\n\n"
        "Permissions: role=ADMIN or role=VENDOR_ADMIN with is_staff=True."
    ),
    request=inline_serializer(
        name="ChangePasswordRequest",
        fields={
            "old_password": serializers.CharField(
                help_text=(
                    "Purpose: Verifies that the requester knows the current password before allowing a change — "
                    "prevents unauthorized password replacement. "
                    "Location: Request body. "
                    "Format: string, plain text, must match the admin's current stored password. "
                    "Required."
                )
            ),
            "new_password": serializers.CharField(
                help_text=(
                    "Purpose: The replacement password to set on the account. "
                    "Location: Request body. "
                    "Format: string, plain text — must differ from old_password; stored as bcrypt hash. "
                    "Required."
                )
            ),
        },
    ),
    responses={
        200: inline_serializer(
            name="ChangePasswordSuccess",
            fields={
                "status": serializers.CharField(help_text="Outcome indicator. Value: 'success'."),
                "message": serializers.CharField(help_text="Human-readable confirmation, e.g. 'Password changed successfully.'"),
            },
        ),
        400: inline_serializer(
            name="ChangePasswordBadRequest",
            fields={
                "status": serializers.CharField(help_text="Outcome indicator. Value: 'error'."),
                "message": serializers.CharField(help_text="Short error summary, e.g. 'Incorrect current password' or 'New password cannot be the same as the old password.'"),
                "errors": serializers.DictField(
                    child=serializers.ListField(child=serializers.CharField()),
                    required=False,
                    help_text="Field-level validation errors keyed by field name. Present only on validation failures.",
                ),
            },
        ),
        404: inline_serializer(
            name="ChangePasswordNotFound",
            fields={
                "status": serializers.CharField(help_text="Outcome indicator. Value: 'error'."),
                "message": serializers.CharField(help_text="Value: 'User not found'."),
            },
        ),
    },
    examples=[
        OpenApiExample(
            "Request",
            value={"old_password": "currentPass123", "new_password": "newSecurePass456"},
            request_only=True,
        ),
    ],
)
@action(
    detail=False,
    methods=["post"],
    url_path="change-password",
    url_name="change-password",
    # Vendor Admins must be able to rotate their own password too; the rest of
    # AdminViewSet stays ADMIN-only (IsAdminUserOnly in base.py).
    permission_classes=[IsAdminPanelUser],
)
def change_password(self, request):
    try:
        user = User.objects.get(id=request.user.id)
    except User.DoesNotExist:
        return self.not_found_response(
            message="User not found",
            dev_message=f"User with ID {request.user.id} not found",
            resource="user"
        )

    old_password = request.data.get("old_password")
    new_password = request.data.get("new_password")
    # logout_all = request.data.get("logout_from_all_devices", False)

    if not old_password or not new_password:
        return self.validation_error_response(
            errors={
                "old_password": ["This field is required."] if not old_password else None,
                "new_password": ["This field is required."] if not new_password else None
            },
            message="Both old and new passwords are required",
            dev_message="Missing required password fields"
        )

    if not user.check_password(old_password):
        return self.error_response(
            message="Incorrect current password",
            dev_message="Provided old password doesn't match current password",
            http_status=status.HTTP_400_BAD_REQUEST
        )

    if user.check_password(new_password):
        return self.validation_error_response(
            errors={"new_password": ["New password cannot be the same as the old password."]},
            message="New password cannot be the same as the old password.",
            dev_message="Attempted to reuse the same password"
        )

    user.set_password(new_password)
    # if logout_all:
    user.password_changed_at = timezone.now()
    user.save()

    # Handle logout from all devices
    message = "Password updated successfully"
    dev_message = "Admin password has been updated"

    # if logout_all:
    #     message += ". You have been logged out from all devices."
    #     dev_message += f" password_changed_at set to {user.password_changed_at}"

    return self.success_response(
        message=message,
        dev_message=dev_message
    )
