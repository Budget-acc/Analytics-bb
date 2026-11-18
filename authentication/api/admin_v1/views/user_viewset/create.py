from rest_framework import status
from rest_framework.response import Response
from authentication.api.admin_v1.serializers import UserSerializer
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers


@extend_schema(
    tags=["Authentication Admin v1"],
    summary="Create user (admin)",
    description=(
        "Creates a new non-superuser account from the admin panel — either a regular user or an admin/staff user.\n\n"
        "Purpose: Allows admins to onboard staff, owners, or other admin users by provisioning their login "
        "credentials, role, and admin status.\n\n"
        "Context: The phone number becomes the login identifier and must be unique across all accounts. "
        "The password is stored as a bcrypt hash — never in plain text. Pass is_staff=true to create an "
        "admin/staff user; omit it (or pass false) to create a regular user, which is the default and "
        "unchanged prior behavior. "
        "To create a Vendor Admin (Marketplace/Vendor admin section only), pass role=VENDOR_ADMIN with is_staff=true; is_staff=true together with is_active=true is what approves the account for the admin-side login. "
        "Authentication: JWT Bearer token required."
    ),
    request=inline_serializer(
        name="AdminCreateUserRequest",
        fields={
            "phone": serializers.CharField(
                help_text=(
                    "Purpose: Primary login identifier for the user — must be unique across all accounts. "
                    "Location: Request body. "
                    "Format: string — accepted formats: +91XXXXXXXXXX (E.164), 91XXXXXXXXXX (12-digit with country code), "
                    "0XXXXXXXXXX (11-digit with leading zero), or XXXXXXXXXX (10-digit local). Normalized to E.164 internally. "
                    "Required."
                )
            ),
            "full_name": serializers.CharField(
                help_text=(
                    "Purpose: User's primary display name shown across the application. "
                    "Location: Request body. "
                    "Format: string. "
                    "Required."
                )
            ),
            "last_name": serializers.CharField(
                required=False,
                help_text=(
                    "Purpose: User's family/last name for full identification. "
                    "Location: Request body. "
                    "Format: string. "
                    "Optional."
                )
            ),
            "email": serializers.EmailField(
                required=False,
                help_text=(
                    "Purpose: Alternative contact identifier and notification address. "
                    "Location: Request body. "
                    "Format: string, valid email — must follow user@domain.com pattern. Validated server-side. "
                    "Optional."
                )
            ),
            "role": serializers.CharField(
                help_text=(
                    "Purpose: Defines the user's access level and permissions within the application. "
                    "Location: Request body. "
                    "Format: string — accepted values: OWNER, STAFF, VENDOR_ADMIN. "
                    "VENDOR_ADMIN accounts must also set is_staff=true to be able to log in to the admin panel. "
                    "Required."
                )
            ),
            "password": serializers.CharField(
                help_text=(
                    "Purpose: Login credential for the new account — transmitted in plain text but stored as bcrypt hash. "
                    "Location: Request body. "
                    "Format: string, plain text. "
                    "Required."
                )
            ),
            "is_active": serializers.BooleanField(
                required=False,
                help_text=(
                    "Purpose: Controls whether the user can log in immediately after creation. "
                    "Location: Request body. "
                    "Format: boolean — true (account enabled) or false (account disabled). Default: true. "
                    "Optional."
                )
            ),
            "max_allowed_sessions": serializers.IntegerField(
                required=False,
                help_text=(
                    "Purpose: Maximum number of concurrent active sessions (devices) allowed for this user. "
                    "When this limit is reached, the oldest active session is revoked on the next login. "
                    "Location: Request body. "
                    "Format: positive integer. Default: 1 if omitted. "
                    "Optional."
                )
            ),
            "is_staff": serializers.BooleanField(
                required=False,
                help_text=(
                    "Purpose: Marks the created account as an admin/staff user, granting access to the "
                    "Admin User API and admin-only permissions, instead of a regular user. "
                    "Location: Request body. "
                    "Format: boolean — true (admin/staff user) or false (regular user). Default: false. "
                    "Optional."
                )
            ),
        },
    ),
    responses={
        201: inline_serializer(
            name="AdminCreateUserSuccess",
            fields={
                "id": serializers.IntegerField(help_text="Unique ID assigned to the newly created user."),
                "phone": serializers.CharField(help_text="Normalized E.164 phone number stored for the user."),
                "full_name": serializers.CharField(help_text="User's first/full name."),
                "last_name": serializers.CharField(allow_null=True, help_text="User's last name, or null if not provided."),
                "email": serializers.EmailField(allow_null=True, help_text="Email address, or null if not provided."),
                "role": serializers.CharField(help_text="Role assigned to the user."),
                "is_active": serializers.BooleanField(help_text="Whether the account is currently active."),
                "max_allowed_sessions": serializers.IntegerField(help_text="Maximum number of concurrent active sessions (devices) allowed for this user."),
                "is_staff": serializers.BooleanField(help_text="Whether the created account is an admin/staff user."),
            }
        ),
        400: inline_serializer(
            name="AdminCreateUserValidationError",
            fields={
                "errors": serializers.DictField(
                    child=serializers.ListField(child=serializers.CharField()),
                    help_text="Field-level validation errors keyed by field name, e.g. {\"phone\": [\"This field is required.\"]}",
                )
            },
        ),
    },
    examples=[
        OpenApiExample(
            "Create user request",
            value={
                "phone": "+919876543210",
                "full_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
                "role": "STAFF",
                "password": "SecurePass@123",
                "is_active": True,
                "max_allowed_sessions": 3,
            },
            request_only=True,
        ),
    ],
)
def create(self, request):
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
    return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
