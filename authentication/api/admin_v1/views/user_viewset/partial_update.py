from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from authentication.models import User
from authentication.api.admin_v1.serializers import UserSerializer
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer, OpenApiExample
from rest_framework import serializers


@extend_schema(
    tags=["Authentication Admin v1"],
    summary="Update user partially (admin)",
    description=(
        "Updates one or more fields of an existing user account without replacing the full record. "
        "Works for both regular users and admin/staff users.\n\n"
        "Purpose: Allows admins to edit specific user profile fields or change account status "
        "(e.g. deactivating a user, updating a role, correcting a phone number, promoting/demoting "
        "admin status) without sending the entire payload.\n\n"
        "Context: Only the fields provided in the request body are updated; all others retain their current values. "
        "If a new password is supplied it is re-hashed before being stored. "
        "Authentication: JWT Bearer token required."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=int,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the specific user account to update. "
                "Location: Path parameter (URL segment, bound as 'pk'). "
                "Format: integer, positive — must correspond to an existing user ID. "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="AdminUserPartialUpdateRequest",
        fields={
            "phone": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Updates the user's login identifier. Must remain unique across all accounts. "
                    "Location: Request body. "
                    "Format: string — accepted formats: +91XXXXXXXXXX (E.164), 91XXXXXXXXXX, 0XXXXXXXXXX, or 10-digit local. "
                    "Optional."
                )
            ),
            "full_name": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Updates the user's primary display name. "
                    "Location: Request body. "
                    "Format: string. "
                    "Optional."
                )
            ),
            "last_name": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Updates the user's family/last name. "
                    "Location: Request body. "
                    "Format: string. "
                    "Optional."
                )
            ),
            "email": serializers.EmailField(
                required=False,
                help_text=(
                    "Purpose: Updates the user's contact email address. "
                    "Location: Request body. "
                    "Format: string, valid email — must follow user@domain.com pattern. "
                    "Optional."
                )
            ),
            "role": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Reassigns the user's access level. "
                    "Location: Request body. "
                    "Format: string — accepted values: OWNER, STAFF, VENDOR_ADMIN. "
                    "A VENDOR_ADMIN can log in to the admin panel (Marketplace/Vendor section only) once is_staff=true. "
                    "Optional."
                )
            ),
            "password": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: Sets a new password for the account. "
                    "Location: Request body. "
                    "Format: string, plain text — re-hashed via bcrypt before storage. "
                    "Optional."
                )
            ),
            "is_active": serializers.BooleanField(
                required=False,
                help_text=(
                    "Purpose: Enables or disables the user's ability to log in. "
                    "Location: Request body. "
                    "Format: boolean — true (account enabled) or false (account disabled). "
                    "Optional."
                )
            ),
            "max_allowed_sessions": serializers.IntegerField(
                required=False,
                help_text=(
                    "Purpose: Updates the maximum number of concurrent active sessions (devices) allowed "
                    "for this user. When the limit is reached, the oldest active session is revoked on "
                    "the next login. "
                    "Location: Request body. "
                    "Format: positive integer. "
                    "Optional."
                )
            ),
            "is_staff": serializers.BooleanField(
                required=False,
                help_text=(
                    "Purpose: Promotes the user to an admin/staff account (true) or demotes them back to a "
                    "regular user (false). Uses the same authorization rules as creating admin users. "
                    "For role=VENDOR_ADMIN this is the admin-access approval switch: true allows admin-side login, "
                    "false revokes it. "
                    "Location: Request body. "
                    "Format: boolean. "
                    "Optional."
                )
            ),
        },
    ),
    responses={
        200: inline_serializer(
            name="AdminPartialUpdateSuccess",
            fields={
                "id": serializers.IntegerField(help_text="Unique user ID."),
                "phone": serializers.CharField(help_text="Updated phone number in E.164 format."),
                "full_name": serializers.CharField(help_text="Updated primary display name."),
                "last_name": serializers.CharField(allow_null=True, help_text="Updated last name. Null if not set."),
                "email": serializers.EmailField(allow_null=True, help_text="Updated email address. Null if not set."),
                "role": serializers.CharField(help_text="Updated role."),
                "is_active": serializers.BooleanField(help_text="Updated account active status."),
                "max_allowed_sessions": serializers.IntegerField(help_text="Updated maximum number of concurrent active sessions allowed for this user."),
                "is_staff": serializers.BooleanField(help_text="Updated admin/staff status of the account."),
            }
        ),
        400: inline_serializer(
            name="AdminPartialUpdateValidationError",
            fields={
                "errors": serializers.DictField(
                    child=serializers.ListField(child=serializers.CharField()),
                    help_text="Field-level validation errors keyed by field name.",
                ),
            },
        ),
        404: inline_serializer(
            name="AdminPartialUpdateNotFound",
            fields={"detail": serializers.CharField(help_text="Value: 'Not found.' — returned when no user matches the given ID.")},
        ),
        500: inline_serializer(
            name="AdminPartialUpdateServerError",
            fields={"error": serializers.CharField(help_text="Unexpected server-side error message.")}
        ),
    },
    examples=[
        OpenApiExample(
            "Update email and active state",
            value={"email": "newmail@example.com", "is_active": True},
            request_only=True,
        ),
    ],
)
def partial_update(self, request, pk=None):  # PATCH
    try:
        # user = get_object_or_404(User, pk=pk, is_deleted=False)
        user = get_object_or_404(User, pk=pk)
        raw_password = request.data.get("password")
        serializer = UserSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            user = serializer.save()
            return Response(serializer.data)
        return Response(
            {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        return Response(
            {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
