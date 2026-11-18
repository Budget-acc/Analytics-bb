from rest_framework.serializers import ModelSerializer
from authentication.models import User
from phonenumber_field.phonenumber import to_python
from rest_framework import serializers
from phonenumbers.phonenumberutil import NumberParseException
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
import phonenumbers


class UserSerializer(ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    is_staff = serializers.BooleanField(required=False, default=False)
    # secondary_password = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ["id", "phone", "full_name", "last_name", "email", "role", "password", "is_active", "max_allowed_sessions", "is_staff"]
        extra_kwargs = {
            'password': {'write_only': True},
            # 'secondary_password': {'write_only': True}
        }

    def to_internal_value(self, data):
        phone = data.get("phone")
        if phone:
            phone = str(phone).strip()
            try:
                if phone.startswith('+'):
                    normalized = to_python(phone)
                elif phone.startswith('91') and len(phone) == 12:
                    normalized = to_python('+' + phone)
                elif phone.startswith('0') and len(phone) == 11:
                    normalized = to_python('+91' + phone[1:])
                elif len(phone) == 10:
                    normalized = to_python('+91' + phone)
                else:
                    raise serializers.ValidationError({"phone": "Invalid phone number format."})

                if not normalized or not normalized.is_valid():
                    raise serializers.ValidationError({"phone": "Invalid phone number."})

                # Convert back to string (E.164 format) before assigning
                data["phone"] = str(normalized)

            except NumberParseException:
                raise serializers.ValidationError({"phone": "Invalid phone number."})

        return super().to_internal_value(data)

    def validate(self, attrs):
        # Add password validation here if needed
        password = attrs.get('password')
        email = attrs.get('email')
        if self.instance is None and not password:  # Creating new user
            raise serializers.ValidationError({"password": "Password is required for new users."})
        # Validate email if provided
        if email:
            try:
                validate_email(email)
            except DjangoValidationError:
                raise serializers.ValidationError({"email": "Enter a valid email address."})

        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User.objects.create_user(
            password=password,
            **validated_data
        )
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class UserProfileSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "full_name",
            "last_name",
            "email",
            "role",
        ]
