from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from authentication.models import User
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers


class UserAuthentication(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["User Management V1"],
        summary="User login",
        description=(
            "Authenticates a user using phone number and password and issues JWT tokens.\n\n"
            "Purpose: Entry-point login API for end users before accessing protected User Management V1 APIs.\n\n"
            "Context: Phone input is normalized to +91 format before user lookup. "
            "Returns both refresh and access tokens on successful authentication."
        ),
        request=inline_serializer(
            name="UserLoginRequestV1",
            fields={
                "phone": serializers.CharField(
                    help_text=(
                        "Purpose: Identifies the user account to authenticate. "
                        "Location: Request body. "
                        "Format/Type: string; accepted formats are +91XXXXXXXXXX, 0XXXXXXXXXX, or XXXXXXXXXX (10-digit local). "
                        "Required."
                    )
                ),
                "password": serializers.CharField(
                    help_text=(
                        "Purpose: Verifies user identity against stored password hash. "
                        "Location: Request body. "
                        "Format/Type: string (plain text in request over HTTPS). "
                        "Required."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="UserLoginSuccessV1",
                fields={
                    "refresh": serializers.CharField(help_text="JWT refresh token."),
                    "access": serializers.CharField(help_text="JWT access token to use in Authorization header."),
                },
            ),
            400: inline_serializer(
                name="UserLoginBadRequestV1",
                fields={
                    "error": serializers.CharField(
                        help_text="Validation error message, such as missing fields or invalid phone format."
                    )
                },
            ),
            401: inline_serializer(
                name="UserLoginUnauthorizedV1",
                fields={
                    "error": serializers.CharField(
                        help_text="Authentication failure message for invalid credentials or inactive account."
                    )
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Login request",
                value={"phone": "9876543210", "password": "SecurePass@123"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        phone = request.data.get("phone")
        password = request.data.get("password")

        if not phone or not password:
            return Response(
                {"error": "Phone number and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Normalize phone input (if not already in +91 format)
        if phone.startswith("+91"):
            normalized_phone = phone
        elif phone.startswith("0") and len(phone) == 11:
            normalized_phone = "+91" + phone[1:]
        elif len(phone) == 10:
            normalized_phone = "+91" + phone
        else:
            return Response(
                {
                    "error": "Invalid phone number format. Must be 10 digits or +91 format."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(phone=normalized_phone)
            print("user:", user)
        except User.DoesNotExist:
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": "User account is inactive or blocked."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Check password using Django's built-in hashing
        if not user.check_password(password):
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            },
            status=status.HTTP_200_OK,
        )
