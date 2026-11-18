from rest_framework import serializers
from shop.models import Staff
from shop.utils.phone_validator import validate_and_normalize_phone


class StaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = ["id", "name", "phone", "color", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_phone(self, value):
        """Normalize and validate phone number per shop"""
        if not value:
            raise serializers.ValidationError("Phone number is required.")

        # normalized_phone = normalize_phone(value)
        normalized_phone, _ = validate_and_normalize_phone(value)
        # if not normalized_phone:
        #     raise serializers.ValidationError("Invalid phone number.")

        # Check uniqueness per shop
        shop_id = self.context.get("shop_id")  # pass shop_id from view
        existing_query = Staff.objects.filter(shop_id=shop_id, phone=normalized_phone)

        if self.instance:
            existing_query = existing_query.exclude(pk=self.instance.pk)

        if existing_query.exists():
            raise serializers.ValidationError("This phone number is already registered for this shop.")

        return normalized_phone

    def validate_name(self, value):
        """Validate and clean name"""
        if not value or not value.strip():
            raise serializers.ValidationError("Name cannot be empty.")

        # Remove extra spaces and title case
        cleaned_name = ' '.join(value.strip().split())

        if len(cleaned_name) < 2:
            raise serializers.ValidationError("Name must be at least 2 characters long.")

        return cleaned_name
