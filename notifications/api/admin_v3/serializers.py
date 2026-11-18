from rest_framework import serializers
from notifications.models import Notification

class NotificationSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True, required=False)
    data = serializers.JSONField(required=False)

    class Meta:
        model = Notification
        fields = ['title', 'body', 'data', 'image', 'target']

    def validate_data(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("The 'data' field must be a JSON object (dict).")
        return value

    def validate(self, data):
        target = data.get('target', 'all')
        notification_data = data.get('data', {})

        if target == 'user' and not notification_data.get('user_id'):
            raise serializers.ValidationError("user_id is required in data when target is 'user'")

        if target == 'shop' and not notification_data.get('shop_id'):
            raise serializers.ValidationError("shop_id is required in data when target is 'shop'")

        return data
