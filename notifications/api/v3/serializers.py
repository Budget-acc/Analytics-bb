from rest_framework import serializers
from notifications.models import DeviceToken, Notification, UserNotification


class DeviceTokenSerializer(serializers.ModelSerializer):
    shop_id = serializers.IntegerField(required=False, write_only=True)
    
    class Meta:
        model = DeviceToken
        fields = ('token', 'platform', 'is_active', 'shop_id')

    def validate_platform(self, value):
        if value not in ['android', 'ios']:
            raise serializers.ValidationError("Platform must be either 'android' or 'ios'.")
        return value


class NotificationDetailSerializer(serializers.ModelSerializer):
    """Read-only serializer for Notification content inside UserNotification."""

    class Meta:
        model = Notification
        fields = (
            "id", "title", "body", "data", "image_url",
            "notification_type", "priority",
            "content_type", "object_id",
            "created_at",
        )
        read_only_fields = fields


class UserNotificationSerializer(serializers.ModelSerializer):
    """Serializer for user notification list endpoint."""

    notification = NotificationDetailSerializer(read_only=True)

    class Meta:
        model = UserNotification
        fields = (
            "id", "notification",
            "is_read", "push_status",
            "received_at", "read_at", "clicked_at",
        )
        read_only_fields = fields

