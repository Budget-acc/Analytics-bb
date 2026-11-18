from rest_framework.permissions import IsAuthenticated
from notifications.models import DeviceToken
from notifications.api.v3.serializers import DeviceTokenSerializer
from ERP.utils.mixins import StandardResponseMixin
from rest_framework.decorators import action  
from rest_framework import viewsets
from rest_framework import serializers
from shop.models import ShopUser, Shop
from shop.api.v3.utils.shop import get_active_shop_id
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
import logging

logger = logging.getLogger("api_requests")

class DeviceTokenViewSet(StandardResponseMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Register device token",
        description=(
            "Registers or updates an FCM device token for the authenticated user. "
            "If shop_id is omitted, the API falls back to the user's active shop."
        ),
        request=inline_serializer(
            name="NotificationDeviceTokenRegisterV3Request",
            fields={
                "token": serializers.CharField(required=True, help_text="FCM device token. Must be unique across users."),
                "platform": serializers.ChoiceField(required=True, choices=["android", "ios"], help_text="Mobile platform for the device token."),
                "is_active": serializers.BooleanField(required=False, default=True, help_text="Whether the device token should remain active for push delivery."),
                "shop_id": serializers.IntegerField(required=False, help_text="Optional shop id. If omitted, the active shop from request context is used."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Device token registered successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Please check your input and try again\n"
                    "- This field is required.\n"
                    "- Platform must be either 'android' or 'ios'.\n"
                    "- Invalid shop ID format.\n"
                    "- No active shop found for user.\n"
                    "- This device token is already registered to another user."
                )
            ),
            403: OpenApiResponse(description="Exact message: User not authorized for this shop"),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "token": "fcm_device_token_123456",
                    "platform": "android",
                    "is_active": True,
                    "shop_id": 7,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Device token registered successfully.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid input response",
                value={
                    "status": "validation_error",
                    "message": "Please check your input and try again",
                    "data": {
                        "validation_errors": {
                            "platform": ["Platform must be either 'android' or 'ios'."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Duplicate token response",
                value={
                    "status": "validation_error",
                    "message": "Validation error",
                    "data": {
                        "validation_errors": {
                            "token": ["This device token is already registered to another user."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Unauthorized shop response",
                value={
                    "status": "error",
                    "message": "User not authorized for this shop",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="register")
    def register(self, request):
        serializer = DeviceTokenSerializer(data=request.data)
        if serializer.is_valid():
            token = serializer.validated_data['token']
            is_active = serializer.validated_data.get('is_active', True)
            platform = serializer.validated_data['platform']
            shop_id = serializer.validated_data.get('shop_id')

            if shop_id:
                # Verify shop is associated with user
                try:
                    shop_id = int(shop_id)
                except ValueError:
                    return self.validation_error_response({"shop_id": ["Invalid shop ID format."]})
                shop_user = ShopUser.objects.filter(user=request.user, shop_id=shop_id, deleted=None, is_active=True).first()
                if not shop_user:
                    return self.unauthorized_shop_response(shop_id=shop_id)
                shop = shop_user.shop
            else:
                # fallback to first active shop
                shop_id = get_active_shop_id(request)
                if not shop_id:
                    return self.validation_error_response({"shop_id": ["No active shop found for user."]})                
                
                shop = Shop.objects.get(id=shop_id)

            existing_token = DeviceToken.objects.filter(token=token).exclude(user=request.user).first()
            if existing_token:
                return self.validation_error_response(
                    {"token": ["This device token is already registered to another user."]}
                )            
            DeviceToken.objects.update_or_create(
                token=token,
                defaults={
                    'user': request.user,
                    'platform': platform,
                    'is_active': is_active,
                    'shop': shop
                }
            )
            return self.success_response(message="Device token registered successfully.")

        try:
            shop_ctx = None
            try:
                shop_ctx = int(request.data.get("shop_id")) if request.data.get("shop_id") else get_active_shop_id(request)
            except Exception:
                shop_ctx = None
            client_ip = request.META.get("REMOTE_ADDR") or request.META.get("HTTP_X_FORWARDED_FOR") or "unknown"
            logger.error(
                "Device token registration validation failed",
                extra={
                    "user_id": getattr(request.user, "id", None),
                    "shop_id": shop_ctx,
                    "client_ip": client_ip,
                    "path": request.path,
                    "errors": serializer.errors,
                }
            )
        except Exception as e:
            logger.exception(f"Failed while logging device token validation failure: {str(e)}")

        return self.validation_error_response(
            serializer.errors,
            message="Please check your input and try again"
        )

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Remove device token",
        description="Marks the authenticated user's device token as inactive so it no longer receives push notifications.",
        request=inline_serializer(
            name="NotificationDeviceTokenRemoveV3Request",
            fields={
                "token": serializers.CharField(required=True, help_text="Existing FCM device token to deactivate."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Device token removed successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Validation error\n"
                    "- This field is required."
                )
            ),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "token": "fcm_device_token_123456",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Device token removed successfully.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing token response",
                value={
                    "status": "validation_error",
                    "message": "Validation error",
                    "data": {
                        "validation_errors": {
                            "token": ["This field is required."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="remove")
    def remove(self, request):
        token = request.data.get("token")
        if token:
            DeviceToken.objects.filter(user=request.user, token=token).update(is_active=False)
            return self.success_response(
                data={},
                message="Device token removed successfully."
            )
        return self.validation_error_response(
            {"token": ["This field is required."]}
        )    

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Update device token shop",
        description="Reassigns an existing device token to another shop that belongs to the authenticated user.",
        request=inline_serializer(
            name="NotificationDeviceTokenUpdateShopV3Request",
            fields={
                "token": serializers.CharField(required=True, help_text="Registered FCM device token owned by the current user."),
                "shop_id": serializers.IntegerField(required=True, help_text="Target shop id that must belong to the authenticated user."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message pattern: Device token updated with shop '{shop_name}' successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Token is required to update shop.\n"
                    "- Shop ID is required.\n"
                    "- Invalid shop ID format.\n"
                    "- Invalid shop ID format. Must be an integer.\n"
                    "- Token not associated with your account.\n"
                    "- This field is required.\n"
                    "- This token is not registered for the current user."
                )
            ),
            403: OpenApiResponse(description="Exact message: User not authorized for this shop"),
            500: OpenApiResponse(description="Exact message: Failed to update device token shop."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "token": "fcm_device_token_123456",
                    "shop_id": 9,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Device token updated with shop 'Main Branch' successfully.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing token response",
                value={
                    "status": "validation_error",
                    "message": "Token is required to update shop.",
                    "data": {
                        "validation_errors": {
                            "token": ["This field is required."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Token ownership error",
                value={
                    "status": "validation_error",
                    "message": "Token not associated with your account.",
                    "data": {
                        "validation_errors": {
                            "token": ["This token is not registered for the current user."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Unauthorized shop response",
                value={
                    "status": "error",
                    "message": "User not authorized for this shop",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Failed to update device token shop.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="update-shop")
    def update_shop(self, request):
        """
        Update the shop_id associated with a user's FCM token.
        Request body must include:
        - token: FCM token
        - shop_id: shop ID (mandatory)
        """
        token = request.data.get("token")
        shop_id = request.data.get("shop_id")

        if not token:
            return self.validation_error_response(
                {"token": ["This field is required."]},
                message="Token is required to update shop."
            )
        if not shop_id:
            return self.validation_error_response(
                {"shop_id": ["This field is required."]},
                message="Shop ID is required."
            )

        try:
            shop_id = int(shop_id)
        except ValueError:
            return self.validation_error_response(
                {"shop_id": ["Invalid shop ID format. Must be an integer."]},
                message="Invalid shop ID format."
            )

        # Verify shop belongs to user
        shop_user = ShopUser.objects.filter(
            user=request.user,
            shop_id=shop_id,
            deleted=None,
            is_active=True
        ).first()
        if not shop_user:
            return self.unauthorized_shop_response(shop_id=shop_id)

        shop = shop_user.shop

        # Verify token belongs to user
        try:
            device_token = DeviceToken.objects.get(token=token, user=request.user)
        except DeviceToken.DoesNotExist:
            return self.validation_error_response(
                {"token": ["This token is not registered for the current user."]},
                message="Token not associated with your account."
            )

        try:
            device_token.shop = shop
            device_token.save(update_fields=["shop", "updated_at"])
            return self.success_response(
                message=f"Device token updated with shop '{shop.name}' successfully."
            )
        except Exception as e:
            return self.error_response(
                message="Failed to update device token shop.",
                dev_message=str(e)
            )
