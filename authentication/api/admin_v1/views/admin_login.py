from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from django.conf import settings
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from authentication.permissions import ADMIN_PANEL_ROLES


@method_decorator(
    ratelimit(key="ip", rate="10/m", method="POST", block=True), name="dispatch"
)
class AdminLoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication Admin v1"],
        summary="Admin login",
        description=(
            "Authenticates an admin-panel user (Admin or Vendor Admin) and issues JWT tokens.\n\n"
            "Purpose: Entry point for admin-panel sessions. Validates phone + password credentials, "
            "enforces that the account has role=ADMIN or role=VENDOR_ADMIN and is_staff=True (a Vendor Admin "
            "is approved for admin access by an Admin setting is_staff=True and is_active=True), then returns a "
            "short-lived access token in the response body and a long-lived refresh token as an HttpOnly cookie.\n\n"
            "Role-based routing: the response includes user.role so the frontend can redirect the user to the "
            "right section. ADMIN can access every admin section; VENDOR_ADMIN can access only the "
            "Marketplace/Vendor admin section.\n\n"
            "Context: Use this endpoint before any admin-protected API call. The returned access token "
            "must be sent as a Bearer token in the Authorization header for all subsequent requests.\n\n"
            "Rate limit: 10 POST requests per minute per IP address. Exceeding this returns HTTP 429."
        ),
        request=inline_serializer(
            name="AdminLoginRequest",
            fields={
                "phone": serializers.CharField(
                    help_text=(
                        "Purpose: Uniquely identifies the admin account attempting to log in. "
                        "Location: Request body. "
                        "Format: string — accepted formats: +91XXXXXXXXXX (E.164), 91XXXXXXXXXX (12-digit with country code), "
                        "0XXXXXXXXXX (11-digit with leading zero), or XXXXXXXXXX (10-digit local). "
                        "Required."
                    )
                ),
                "password": serializers.CharField(
                    help_text=(
                        "Purpose: Authenticates the admin's identity by verifying against the stored bcrypt hash. "
                        "Location: Request body. "
                        "Format: string, plain text — transmitted over HTTPS and never stored in plain form. "
                        "Required."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="AdminLoginResponse",
                fields={
                    "access": serializers.CharField(
                        help_text="Short-lived JWT access token. Include as 'Authorization: Bearer <token>' in subsequent requests."
                    ),
                    "user": inline_serializer(
                        name="AdminUserInfo",
                        fields={
                            "id": serializers.IntegerField(help_text="Unique integer ID of the authenticated admin user."),
                            "full_name": serializers.CharField(help_text="Display name of the authenticated admin user."),
                            "role": serializers.ChoiceField(
                                choices=list(ADMIN_PANEL_ROLES),
                                help_text=(
                                    "Role of the authenticated user, used by the frontend to pick the section to open. "
                                    "Values: 'ADMIN' (all admin sections) or 'VENDOR_ADMIN' (Marketplace/Vendor section only)."
                                ),
                            ),
                        },
                    ),
                },
            ),
            400: inline_serializer(
                name="AdminLoginBadRequest",
                fields={
                    "error": serializers.CharField(help_text="Validation message, e.g. 'Phone and password are required.'"),
                },
            ),
            401: inline_serializer(
                name="AdminLoginUnauthorized",
                fields={
                    "error": serializers.CharField(help_text="Credentials did not match any account. Value: 'Invalid credentials.'"),
                },
            ),
            403: inline_serializer(
                name="AdminLoginForbidden",
                fields={
                    "error": serializers.CharField(help_text="Account exists but is not an ADMIN/VENDOR_ADMIN or is not approved (is_staff=False). Value: 'You do not have admin access.'"),
                },
            ),
        },
    )
    def post(self, request):
        phone = request.data.get("phone")
        password = request.data.get("password")

        if not phone or not password:
            return Response(
                {"error": "Phone and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, phone=phone, password=password)

        if not user:
            return Response(
                {"error": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
            )

        # authenticate() already rejects inactive users; is_staff is the
        # admin-access approval flag for both ADMIN and VENDOR_ADMIN.
        if user.role not in ADMIN_PANEL_ROLES or not user.is_staff:
            return Response(
                {"error": "You do not have admin access."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Generate JWT token
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)

        response = Response(
            {
                "access": access_token,
                "user": {
                    "id": user.id,
                    "full_name": user.full_name,
                    "role": user.role,
                },
            },
            status=status.HTTP_200_OK,
        )

        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=settings.COOKIE_HTTPONLY,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            max_age=settings.COOKIE_MAX_AGE,
            domain=settings.COOKIE_DOMAIN,
            path=settings.COOKIE_PATH,
        )

        return response
