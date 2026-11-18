from rest_framework.decorators import action
from rest_framework import serializers
from authentication.models import User
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
import logging

logger = logging.getLogger('api_requests')


@extend_schema(
    tags=["Authentication Admin v1"],
    summary="Reset a user's password (admin)",
    description=(
        "Allows an authenticated admin to forcefully reset the password of any non-superuser account.\n\n"
        "Purpose: Provides an admin-controlled password recovery mechanism — used when a user has lost "
        "access and cannot reset their password themselves.\n\n"
        "Context: Only non-superuser accounts may be targeted. The admin must be authenticated via JWT. "
        "The new password is applied immediately and the user's password_changed_at timestamp is updated, "
        "which invalidates all the user's existing tokens.\n\n"
        "Authentication: JWT Bearer token required in the Authorization header."
    ),
    request=inline_serializer(
        name="ResetPasswordRequest",
        fields={
            "user_id": serializers.IntegerField(
                help_text=(
                    "Purpose: Identifies the target user account whose password will be reset. "
                    "Location: Request body. "
                    "Format: integer, positive — must correspond to an existing, non-superuser account. "
                    "Required."
                )
            ),
            "password": serializers.CharField(
                help_text=(
                    "Purpose: The new password to assign to the target user's account. "
                    "Location: Request body. "
                    "Format: string, plain text — stored as bcrypt hash after assignment. "
                    "Required."
                )
            ),
        },
    ),
    responses={
        200: inline_serializer(
            name="ResetPasswordSuccess",
            fields={
                "status": serializers.CharField(help_text="Outcome indicator. Value: 'success'."),
                "message": serializers.CharField(help_text="Human-readable confirmation, e.g. 'Password reset successfully.'"),
                "data": inline_serializer(
                    name="ResetPasswordData",
                    fields={
                        "user_id": serializers.IntegerField(help_text="ID of the user whose password was reset."),
                    },
                ),
            },
        ),
        400: inline_serializer(
            name="ResetPasswordValidationError",
            fields={
                "status": serializers.CharField(help_text="Outcome indicator. Value: 'error'."),
                "message": serializers.CharField(help_text="Short error summary, e.g. 'Please provide a valid user ID.'"),
                "errors": serializers.DictField(
                    child=serializers.ListField(child=serializers.CharField()),
                    required=False,
                    help_text="Field-level validation errors keyed by field name. Present only on validation failures.",
                ),
            },
        ),
        404: inline_serializer(
            name="ResetPasswordNotFound",
            fields={
                "status": serializers.CharField(help_text="Outcome indicator. Value: 'error'."),
                "message": serializers.CharField(help_text="Value: 'User not found' — returned when user_id does not match a non-superuser account."),
            },
        ),
    },
    examples=[
        OpenApiExample(
            "Request",
            value={"user_id": 5, "password": "NewSecurePass123"},
            request_only=True,
        ),
    ],
)
@action(detail=False, methods=["post"], url_path="reset-password")
def reset_password(self, request):
    try:
        user_id = request.data.get("user_id")
        if not user_id:
            return self.validation_error_response(
                errors={"user_id": ["User ID is required."]},
                message="Please provide a valid user ID.",
                dev_message="Missing user_id in request body."
            )

        user = User.objects.get(pk=user_id, is_superuser=False)

        # Extract new password
        new_password = request.data.get("password")
        if not new_password:
            return self.validation_error_response(
                errors={"password": ["Password is required."]},
                message="Please provide a new password.",
                dev_message="Password field missing in request body."
            )

        # Update password
        user.set_password(new_password)
        user.password_changed_at = timezone.now()
        user.save()

        return self.success_response(
            data={"user_id": user.id},
            message="Password has been reset successfully.",
            dev_message=f"Password updated for user {user.id}"
        )

    except User.DoesNotExist:
        return self.not_found_response(
            resource="User",
            message="User not found.",
            dev_message=f"User with id {user_id} does not exist."
        )
    except Exception as e:
        logger.error(f"Error resetting password for user {user_id}: {str(e)}")
        return self.error_response(
            message="Unable to reset password. Please try again later.",
            dev_message=str(e)
        )
