from rest_framework import viewsets, status
from rest_framework.decorators import action
from authentication.permissions import IsAdminUserOnly
from rest_framework.response import Response
from ERP.utils.mixins import StandardResponseMixin
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from notifications.models import Notification
from notifications.api.v3.utils.firebase_utils import send_targeted_notification
from .serializers import NotificationSerializer  
from services.api.v3.utils.s3 import upload_image_to_s3 
from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import MultiPartParser, FormParser 
import logging

logger = logging.getLogger(__name__)


class AdminNotificationViewSet(StandardResponseMixin, viewsets.ViewSet):
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=["Notifications Admin Management V3"],
        summary="Send admin notification",
        description="Creates a notification record and sends a targeted push notification to all users, a specific user, or a specific shop.",
        request=inline_serializer(
            name="NotificationsAdminV3SendNotificationRequest",
            fields={
                "title": serializers.CharField(required=True, help_text="Notification title text."),
                "body": serializers.CharField(required=True, help_text="Notification message body."),
                "target": serializers.CharField(required=False, default="all", help_text="Target scope. Common values: all, user, shop."),
                "data": serializers.JSONField(required=False, help_text="Additional payload object. Required keys by target: user -> user_id, shop -> shop_id."),
                "image": serializers.ImageField(required=False, help_text="Optional image file uploaded and attached to notification."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Success response with notification id and delivery counts."),
            400: OpenApiResponse(
                description=(
                    "Validation messages (point by point):\n"
                    "- user_id is required in data when target is 'user'\n"
                    "- shop_id is required in data when target is 'shop'\n"
                    "- The 'data' field must be a JSON object (dict).\n"
                    "- serializer field-level errors (for example missing required title/body, invalid file) returned exactly as serializer output."
                )
            ),
            500: OpenApiResponse(
                description=(
                    "Error messages (point by point):\n"
                    "- Failed to upload image.\n"
                    "- Failed to send notification. No active device tokens found or Firebase error occurred.\n"
                    "- Failed to send notification due to server error."
                )
            ),
        },
        examples=[
            OpenApiExample(
                "Request example (target user)",
                value={
                    "title": "Payment Reminder",
                    "body": "Your pending amount is due today.",
                    "target": "user",
                    "data": {"user_id": 42, "type": "payment_due"}
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Notification sent successfully. Success: 12, Failed: 1",
                    "data": {
                        "notification_id": 101,
                        "success_count": 12,
                        "failure_count": 1
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error (missing user_id)",
                value={
                    "status": "validation_error",
                    "message": "Validation failed",
                    "errors": ["user_id is required in data when target is 'user'"]
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Failure response",
                value={
                    "status": "error",
                    "message": "Failed to send notification. No active device tokens found or Firebase error occurred.",
                    "data": None
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="send")
    def send_notification(self, request):
        serializer = NotificationSerializer(data=request.data)
        if not serializer.is_valid():
            return self.validation_error_response(serializer.errors)

        title = serializer.validated_data['title']
        body = serializer.validated_data['body']
        data = serializer.validated_data.get('data', {})
        target = serializer.validated_data.get('target', 'all')
        image_file = serializer.validated_data.get('image', None)
        
        # Extract target-specific IDs from data
        user_id = data.get('user_id') if target == 'user' else None
        print("user_id:", user_id)
        shop_id = data.get('shop_id') if target == 'shop' else None

        image_url = None
        if image_file:
            try:
                image_url = upload_image_to_s3(image_file, path_prefix="admin/notifications")
            except Exception as e:
                logger.error(f"Failed to upload image to S3: {e}")
                return self.error_response(message="Failed to upload image.")

        # Save notification in DB
        notification = Notification.objects.create(
            title=title,
            body=body,
            data=data,
            image_url=image_url,
            target=target,
            created_by=request.user
        )

        try:
            # Send targeted notification
            result = send_targeted_notification(
                title=title,
                body=body,
                target=target,
                data=data,
                image=image_url,
                user_id=user_id,
                shop_id=shop_id
            )

            if result and result.get('success_count', 0) > 0:
                notification.sent_at = timezone.now()
                notification.save(update_fields=["sent_at"])
                
                response_message = f"Notification sent successfully. "
                response_message += f"Success: {result['success_count']}, Failed: {result['failure_count']}"
                
                return self.success_response(
                    data={
                        "notification_id": notification.id,
                        "success_count": result['success_count'],
                        "failure_count": result['failure_count']
                    },
                    message=response_message
                )
            else:
                return self.error_response(
                    message="Failed to send notification. No active device tokens found or Firebase error occurred."
                )
                
        except Exception as e:
            logger.error(f"Error sending notification: {e}")
            return self.error_response(message="Failed to send notification due to server error.")


# def test_firebase_connection():
#     """
#     Test function to validate Firebase configuration
#     """
#     try:
#         app = get_firebase_app()
        
#         # Try to create a test message (don't send)
#         test_message = messaging.Message(
#             notification=messaging.Notification(
#                 title="Test",
#                 body="Connection test"
#             ),
#             token="test_token"
#         )
        
#         logger.info("Firebase connection test passed")
#         return True
        
#     except Exception as e:
#         logger.error(f"Firebase connection test failed: {e}")
#         return False