import logging

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from authentication.models import User
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from authentication.session_manager import logout_all_devices
from django.db.models import F
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers

logger = logging.getLogger("api_requests")


class ChangePasswordAPIView(APIView, StandardResponseMixin):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["User Management V3"],
        summary="Change password",
        description=(
            "Changes the authenticated user's primary login password.\n\n"
            "Purpose: Secure credential rotation for user accounts.\n\n"
            "Context: Requires current password verification. If logout_from_all_devices is true, "
            "password_changed_at is updated so older tokens become invalid."
        ),
        request=inline_serializer(
            name="ChangePasswordRequestV3",
            fields={
                "old_password": serializers.CharField(
                    help_text=(
                        "Purpose: Confirms the user's existing password before change. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
                "new_password": serializers.CharField(
                    help_text=(
                        "Purpose: Sets the new login password for the user account. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
                "logout_from_all_devices": serializers.BooleanField(
                    required=False,
                    default=False,
                    help_text=(
                        "Purpose: Invalidates existing device sessions/tokens when true. "
                        "Location: Request body. "
                        "Format/Type: boolean. "
                        "Optional. Default: false."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="ChangePasswordSuccessV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Password update success message."),
                    "dev_message": serializers.CharField(help_text="Developer detail for updated state."),
                    "data": serializers.DictField(required=False),
                },
            ),
            400: inline_serializer(
                name="ChangePasswordValidationErrorV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Validation error summary."),
                    "dev_message": serializers.CharField(help_text="Developer detail for validation error."),
                    "errors": serializers.DictField(
                        child=serializers.ListField(child=serializers.CharField()),
                        required=False,
                    ),
                },
            ),
            404: inline_serializer(
                name="ChangePasswordNotFoundV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "dev_message": serializers.CharField(),
                    "resource": serializers.CharField(),
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Change password request",
                value={
                    "old_password": "OldPass@123",
                    "new_password": "NewPass@456",
                    "logout_from_all_devices": True,
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
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
        logout_all = request.data.get("logout_from_all_devices", False)

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
                dev_message="Provided old password doesn't match user's current password",
                http_status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        update_fields = ["password"]
        if logout_all:
            user.password_changed_at = timezone.now()  # audit-only now; kept for legacy-token fallback
            update_fields.append("password_changed_at")
        user.save(update_fields=update_fields)

        if logout_all:
            # Atomic DB-side increment (not a Python `user.session_version += 1`
            # followed by a blanket save()) so two concurrent logout-all
            # requests for this user can't lose one increment to a
            # last-write-wins race - the DB row is the source of truth this
            # is written from.
            User.objects.filter(id=user.id).update(session_version=F("session_version") + 1)
            user.refresh_from_db(fields=["session_version"])
            # Write-through the now-durable session_version into Redis and
            # clear the session registry - O(1), no per-session enumeration,
            # no DB session table to query. Best-effort: the DB write above
            # (password_changed_at / session_version) is already the
            # authoritative invalidation mechanism, so a Redis outage here
            # must not fail this request - only log it.
            redis_ok = logout_all_devices(user)
            if not redis_ok:
                logger.warning(
                    f"Redis unavailable during logout-all-devices for user {user.id}; "
                    "relying on the password_changed_at DB fallback for invalidation."
                )

        # Handle logout from all devices
        message = "Password updated successfully"
        dev_message = "User password has been updated"

        if logout_all:
            message += ". You have been logged out from all devices."
            dev_message += f" password_changed_at set to {user.password_changed_at}"

        return self.success_response(
            message=message,
            dev_message=dev_message
        )
