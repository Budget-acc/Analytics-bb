from rest_framework import serializers
from tailored.models import Tailor


class TailorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tailor
        fields = ["id", "name", "phone", "is_deleted", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {
            "is_deleted": {"write_only": True},
        }

    def validate_phone(self, value):
        """Normalize and validate phone number per shop"""

        shop_id = self.context.get("shop_id")
        existing_query = Tailor.objects.filter(shop_id=shop_id, phone=value)

        if self.instance:
            existing_query = existing_query.exclude(pk=self.instance.pk)

        if existing_query.exists():
            # use a clear message and an optional code
            raise serializers.ValidationError("This phone number is already registered for this shop.", code="unique")

        return value

    def validate_name(self, value):
        """Validate and clean name"""
        if not value or not value.strip():
            raise serializers.ValidationError("Name cannot be empty.")

        # Remove extra spaces and title case
        cleaned_name = ' '.join(value.strip().split())

        if len(cleaned_name) < 2:
            raise serializers.ValidationError("Name must be at least 2 characters long.")

        return cleaned_name
