from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from authentication.models import User
from authentication.tokens import issue_session_tokens
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.response_utils import ResponseMessage
import phonenumbers
from phonenumbers import NumberParseException
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers


class UserAuthentication(APIView, StandardResponseMixin):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["User Management V3"],
        summary="User login",
        description=(
            "Authenticates a user account and returns JWT access and refresh tokens.\n\n"
            "Purpose: Main login endpoint for user-facing v3 flows.\n\n"
            "Context: The phone number is normalized to E.164 format before lookup. "
            "Optional device_token and platform fields register/update an active device token for notifications."
        ),
        request=inline_serializer(
            name="UserLoginRequestV3",
            fields={
                "phone": serializers.CharField(
                    help_text=(
                        "Purpose: Identifies which user account is attempting login. "
                        "Location: Request body. "
                        "Format/Type: string phone number; accepted formats include +91XXXXXXXXXX, 0XXXXXXXXXX, XXXXXXXXXX, or country code without plus; normalized to E.164. "
                        "Required."
                    )
                ),
                "password": serializers.CharField(
                    help_text=(
                        "Purpose: Verifies user identity against stored hashed password. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
                "device_token": serializers.CharField(
                    required=False,
                    help_text=(
                        "Purpose: Registers Firebase/device token for push notifications. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Optional."
                    )
                ),
                "platform": serializers.CharField(
                    required=False,
                    help_text=(
                        "Purpose: Indicates client platform for token registration (for example android/ios/web). "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Optional."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="UserLoginSuccessV3",
                fields={
                    "status": serializers.CharField(help_text="Operation status. Example: success."),
                    "message": serializers.CharField(help_text="Human-readable success message."),
                    "dev_message": serializers.CharField(help_text="Developer-oriented details for the login operation."),
                    "data": inline_serializer(
                        name="UserLoginDataV3",
                        fields={
                            "refresh": serializers.CharField(help_text="JWT refresh token."),
                            "access": serializers.CharField(help_text="JWT access token used in Authorization: Bearer <token>."),
                        },
                    ),
                },
            ),
            400: inline_serializer(
                name="UserLoginValidationErrorV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Validation failure summary."),
                    "dev_message": serializers.CharField(help_text="Technical validation details."),
                    "errors": serializers.DictField(
                        child=serializers.ListField(child=serializers.CharField()),
                        help_text="Field-level validation errors keyed by request field.",
                    ),
                },
            ),
            401: inline_serializer(
                name="UserLoginUnauthorizedV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Authentication failure message."),
                    "dev_message": serializers.CharField(help_text="Developer-oriented reason for unauthorized response."),
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Basic login",
                value={"phone": "9876543210", "password": "SecurePass@123"},
                request_only=True,
            ),
            OpenApiExample(
                "Login with device token",
                value={
                    "phone": "+919876543210",
                    "password": "SecurePass@123",
                    "device_token": "fcm_token_here",
                    "platform": "android",
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        phone = request.data.get("phone")
        password = request.data.get("password")
        device_token = request.data.get("device_token")
        platform = request.data.get("platform")

        # Validate required fields
        if not phone or not password:
            missing_fields = []
            if not phone: missing_fields.append("phone")
            if not password: missing_fields.append("password")

            return self.validation_error_response(
                errors={field: ["This field is required."] for field in missing_fields},
                message="Required fields are missing",
                dev_message=f"Missing required fields: {', '.join(missing_fields)}"
            )

        # Normalize & Validate Phone
        normalized_phone = None

        try:
            raw_phone = str(phone).strip()

            # Case 1: If starts with '+', parse directly
            if raw_phone.startswith("+"):
                parsed = phonenumbers.parse(raw_phone, None)

            # Case 2: 11-digit starting with 0 → remove 0, assume India
            elif raw_phone.startswith("0") and len(raw_phone) == 11:
                parsed = phonenumbers.parse(raw_phone[1:], "IN")

            # Case 3: 10-digit → assume India
            elif len(raw_phone) == 10 and raw_phone.isdigit():
                parsed = phonenumbers.parse(raw_phone, "IN")

            # Case 4: Country code without '+'
            else:
                parsed = phonenumbers.parse("+" + raw_phone, None)

            if not phonenumbers.is_valid_number(parsed):
                raise ValueError("Invalid phone number")

            # Convert to E.164 format
            normalized_phone = phonenumbers.format_number(
                parsed,
                phonenumbers.PhoneNumberFormat.E164
            )

        except (NumberParseException, ValueError):
            return self.validation_error_response(
                errors={"phone": ["Invalid phone number format."]},
                message=ResponseMessage.INVALID_PHONE,
                dev_message="Invalid or unsupported phone number."
            )

        try:
            user = User.objects.get(phone=normalized_phone)
        except User.DoesNotExist:
            return self.error_response(
                message="User not found",
                dev_message=f"No user found with phone: {normalized_phone}",
                http_status=status.HTTP_401_UNAUTHORIZED
            )

        if not user.is_active:
            return self.error_response(
                message="Account is inactive",
                dev_message=f"User account {user.id} is marked inactive",
                http_status=status.HTTP_401_UNAUTHORIZED
            )

        if not user.check_password(password):
            return self.error_response(
                message="Incorrect password",
                dev_message="Password did not match for existing user",
                http_status=status.HTTP_401_UNAUTHORIZED
            )

        # max_allowed_sessions=0 is a deliberate admin setting meaning "block
        # all new sessions for this user" - rejected here, before any token
        # is issued, rather than inside create_session/issue_session_tokens.
        # Returning None from create_session (as if Redis were unavailable)
        # would be the wrong signal here: that path issues a legacy token
        # with no session_id claim, which would give this user a token that
        # bypasses session tracking entirely - worse than doing nothing.
        if user.max_allowed_sessions == 0:
            return self.error_response(
                message="This account is not allowed to log in",
                dev_message=f"User {user.id} has max_allowed_sessions=0 (login blocked by admin)",
                http_status=status.HTTP_403_FORBIDDEN
            )

        refresh, access = issue_session_tokens(user, platform=platform or "")

        dev_message = "Login successful"
        if device_token and platform:
            from notifications.models import DeviceToken
            from shop.models import ShopUser

            shop_user = (
                ShopUser.objects.filter(user=user, deleted=None, is_active=True)
                .select_related("shop")
                .order_by("created_at")
                .first()
            )
            shop = shop_user.shop if shop_user and shop_user.shop and shop_user.shop.is_active else None


            obj, created = DeviceToken.objects.update_or_create(
                token=device_token,
                defaults={
                    "user": user,
                    "platform": platform,
                    "is_active": True,
                    "shop": shop
                }
            )
            if not created:
                obj.shop = shop
                obj.is_active = True
                obj.user = user
                obj.platform = platform
                obj.save(update_fields=["shop", "is_active", "user", "platform", "updated_at"])
                dev_message = "Login successful with FCM token registered (updated)"
            else:
                dev_message = "Login successful with FCM token registered (created)"

        return self.success_response(
            data={
                "refresh": str(refresh),
                "access": str(access),
            },
            message="Login successful",
            dev_message=dev_message
        )
