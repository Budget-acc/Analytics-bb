from rest_framework import serializers
from shop.models import Shop, ShopUser
from authentication.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from shop.utils.phone_validator import validate_and_normalize_phone

def normalize_phone(phone):
    """
    Normalize phone to a 10-digit string.
    Valid if:
    - Exactly 10 digits
    - Does not start with 0
    """
    try:
        phone = str(phone).strip()
        # Remove country code if exists
        if phone.startswith("+91"):
            phone = phone[3:]
        elif phone.startswith("91") and len(phone) > 10:
            phone = phone[2:]
        elif phone.startswith("0"):
            phone = phone[1:]

        # Only digits
        if not phone.isdigit():
            return None

        # Must be exactly 10 digits and not start with 0
        if len(phone) != 10 or phone[0] == "0":
            return None

        return phone
    except Exception:
        return None


class ShopSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(default=True)

    class Meta:
        model = Shop
        fields = [
            "id", "name", "phone", "email", "address", "city", "state", "pincode",
            "gst_number", "img", "place", "subscription_status", "terms_and_conditions", "extra_stock_limit",
            "subscribed_date", "renewal_due_date", "created_at",  "is_active",
        ]
        read_only_fields = ["id", "created_at"]

class ShopListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            "id", "name", "phone", "email",
            "img", "place", "subscription_status",
             "created_at",  "is_active",
        ]
        read_only_fields = ["id", "created_at"]

class ShopUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            "id", "name", "place", "phone", "phone2", "email", "address",
            "gst_number", "city", "state", "pincode",
            "subscription_status", "subscribed_date", "renewal_due_date",
            "img"
        ]


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    secondary_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "full_name",
            "last_name",
            "email",
            "role",
            "password",
            "secondary_password",
        ]

    def to_internal_value(self, data):
        phone = data.get("phone")
        if phone:
            try:
                data["phone"] = normalize_phone(phone)
            except ValueError as e:
                raise serializers.ValidationError({"phone": str(e)})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if not attrs.get("password"):
            raise serializers.ValidationError({"password": "Password is required."})
        if len(attrs["password"]) < 6:
            raise serializers.ValidationError({"password": "Password must be at least 6 characters."})
        if not attrs.get("secondary_password"):
            raise serializers.ValidationError({"secondary_password": "Secondary password is required."})

        email = attrs.get("email")
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise serializers.ValidationError({"email": "Enter a valid email address."})

            queryset = User.objects.filter(email=email)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError({"email": "Email already in use."})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        secondary_password = validated_data.pop("secondary_password")
        user = User.objects.create_user(
            password=password,
            secondary_password=secondary_password,
            **validated_data
        )
        return user

class ShopUserLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopUser
        fields = ['shop', 'user', 'role', 'is_active']

    def validate(self, attrs):
        shop = attrs.get("shop")
        user = attrs.get("user")

        if ShopUser.objects.filter(shop=shop, user=user).exists():
            raise serializers.ValidationError("This user is already linked to the shop.")

        return attrs
