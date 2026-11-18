from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from authentication.models import User
from authentication.api.admin_v1.serializers import UserSerializer
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers


@extend_schema(
    tags=["Authentication Admin v1"],
    summary="Get user details (admin)",
    description=(
        "Fetches the full profile of a single user by their ID. Works for both regular users and "
        "admin/staff users.\n\n"
        "Purpose: Provides the complete user record needed to populate an admin edit form or display a user's profile detail page.\n\n"
        "Context: Returns all non-sensitive fields for the specified user. The password field is write-only "
        "and is never included in the response. "
        "Authentication: JWT Bearer token required."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=int,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the specific user account to retrieve. "
                "Location: Path parameter (URL segment, bound as 'pk'). "
                "Format: integer, positive — must correspond to an existing user ID. "
                "Required."
            ),
        ),
    ],
    responses={
        200: inline_serializer(
            name="AdminRetrieveUserSuccess",
            fields={
                "id": serializers.IntegerField(help_text="Unique user ID."),
                "phone": serializers.CharField(help_text="Phone number in E.164 format, e.g. +919876543210."),
                "full_name": serializers.CharField(help_text="User's primary display name."),
                "last_name": serializers.CharField(allow_null=True, help_text="User's last name. Null if not set."),
                "email": serializers.EmailField(allow_null=True, help_text="Email address. Null if not set."),
                "role": serializers.CharField(help_text="Role assigned to the user, e.g. OWNER or STAFF."),
                "is_active": serializers.BooleanField(help_text="Whether the account is enabled for login."),
                "max_allowed_sessions": serializers.IntegerField(
                    help_text="Maximum number of concurrent active sessions (devices) allowed for this user."
                ),
                "is_staff": serializers.BooleanField(help_text="Whether the account is an admin/staff account."),
            }
        ),
        404: inline_serializer(
            name="AdminRetrieveUserNotFound",
            fields={"detail": serializers.CharField(help_text="Value: 'Not found.' — returned when no user matches the given ID.")},
        ),
        400: inline_serializer(
            name="AdminRetrieveUserError",
            fields={"error": serializers.CharField(help_text="Unexpected server-side error message.")}
        ),
    },
)
def retrieve(self, request, pk=None):
    try:
        # user = get_object_or_404(User, pk=pk, is_deleted=False)
        user = get_object_or_404(User, pk=pk)
        serializer = UserSerializer(user)
        return Response(serializer.data)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
