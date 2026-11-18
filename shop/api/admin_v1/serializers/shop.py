from rest_framework import serializers
from django.db.models import Max
from django.db.models import IntegerField as _IntegerField
from django.db.models.functions import Cast, Substr
from shop.models import Shop, ShopUser, ShopNumberingSequence
from authentication.api.v1.utils import normalize_phone
from django.core.validators import validate_email
from django.core.exceptions import ValidationError


class ShopSerializer(serializers.ModelSerializer):
    secret_password = serializers.CharField(
        source='password',
        required=False,
        allow_blank=False,
        max_length=128,
        write_only=False
    )
    is_active = serializers.BooleanField(default=True)
    total_product_count = serializers.SerializerMethodField()
    subscription_status = serializers.SerializerMethodField()
    organization = serializers.SerializerMethodField()

    # NUMBERING_FIELDS: these 10 keys are no longer real Shop model columns
    # as far as this serializer is concerned - Shop still HAS the first 8
    # as actual (now-stale, unused) columns, but ShopNumberingSequence is
    # the source of truth for all 10. Declared explicitly here (instead of
    # relying on ModelSerializer's auto-generated mapping) so reads/writes
    # route through create()/update()/to_representation() below rather than
    # the legacy Shop columns. The request/response keys are unchanged.
    booking_start_id = serializers.IntegerField(required=False)
    sale_start_id = serializers.IntegerField(required=False)
    tailor_start_id = serializers.IntegerField(required=False)
    purchase_start_id = serializers.IntegerField(required=False)
    last_booking_id = serializers.IntegerField(required=False)
    last_sale_id = serializers.IntegerField(required=False)
    last_tailor_id = serializers.IntegerField(required=False)
    last_purchase_id = serializers.IntegerField(required=False)
    last_product_number = serializers.IntegerField(required=False)
    product_code_prefix = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    NUMBERING_FIELDS = (
        "booking_start_id", "sale_start_id", "tailor_start_id", "purchase_start_id",
        "last_booking_id", "last_sale_id", "last_tailor_id", "last_purchase_id",
        "last_product_number", "product_code_prefix",
    )

    class Meta:
        model = Shop
        fields = [
            "id", "name", "phone", "phone2", "email", "address", "city", "state", "pincode", "booking_start_id", "sale_start_id", "tailor_start_id", "purchase_start_id",
            "last_booking_id", "last_sale_id", "last_tailor_id", "last_purchase_id", "last_product_number", "product_code_prefix",
            "gst_number", "img", "place", "subscription_status", "terms_and_conditions", "extra_stock_limit", "secret_password",
            "subscribed_date", "renewal_due_date", "created_at",  "is_active", "total_product_count", "extra_user_limit",
            "subscription_renewal_price", "organization", "show_prices_in_public_listing",
        ]
        read_only_fields = ["id", "created_at", "total_product_count", "organization"]

    def create(self, validated_data):
        numbering_data = {f: validated_data.pop(f) for f in self.NUMBERING_FIELDS if f in validated_data}
        shop = super().create(validated_data)
        # Shop.save()'s _ensure_product_code_prefix() hook already created a
        # bare ShopNumberingSequence row for this shop by this point - only
        # update it if the client actually supplied any numbering fields,
        # so an unrelated create request never touches the auto-generated
        # product_code_prefix.
        if numbering_data:
            ShopNumberingSequence.objects.update_or_create(shop=shop, defaults=numbering_data)
        return shop

    def update(self, instance, validated_data):
        numbering_data = {f: validated_data.pop(f) for f in self.NUMBERING_FIELDS if f in validated_data}
        shop = super().update(instance, validated_data)
        if numbering_data:
            ShopNumberingSequence.objects.update_or_create(shop=shop, defaults=numbering_data)
        return shop

    def to_representation(self, instance):
        data = super().to_representation(instance)
        sequence = getattr(instance, "numbering_sequence", None)
        for field in self.NUMBERING_FIELDS:
            default = None if field == "product_code_prefix" else 0
            data[field] = getattr(sequence, field, default) if sequence else default
        return data

    def get_total_product_count(self, obj):
        """Get total count of products across all services for this shop"""
        return getattr(obj, 'total_product_count', 0)

    def validate_booking_start_id(self, value):
        """Ensure booking_start_id is a positive integer (>= 0)."""
        if value is None:
            return 0
        if not isinstance(value, int):
            raise serializers.ValidationError("Booking start ID must be an integer.")
        if value < 0:
            raise serializers.ValidationError("Booking start ID must be a positive integer.")
        return value

    def validate_sale_start_id(self, value):
        """Ensure sale_start_id is a positive integer (>= 0)."""
        if value is None:
            return 0
        if not isinstance(value, int):
            raise serializers.ValidationError("Sale start ID must be an integer.")
        if value < 0:
            raise serializers.ValidationError("Sale start ID must be a positive integer.")
        return value

    def validate_purchase_start_id(self, value):
        """Ensure purchase_start_id is a positive integer (>= 0)."""
        if value is None:
            return 0
        if not isinstance(value, int):
            raise serializers.ValidationError("Purchase start ID must be an integer.")
        if value < 0:
            raise serializers.ValidationError("Purchase start ID must be a positive integer.")
        return value

    def validate_last_purchase_id(self, value):
        """
        Ensure last_purchase_id is a positive integer (>= 0) and, unlike
        last_booking_id/last_sale_id/last_tailor_id, is not set below this
        shop's actual current max purchase_code - Purchase.purchase_code is
        DB-uniquely constrained per shop (unique_purchase_code_per_shop), so
        a lowered counter would make every subsequent purchase creation for
        this shop fail with IntegrityError until corrected, unlike the other
        three counters which have no such DB constraint.
        """
        if value is None:
            return 0
        if not isinstance(value, int):
            raise serializers.ValidationError("Last purchase ID must be an integer.")
        if value < 0:
            raise serializers.ValidationError("Last purchase ID must be a positive integer.")

        if self.instance is not None:
            from purchase.models import Purchase
            current_max = (
                Purchase.all_objects.filter(
                    shop=self.instance, purchase_code__regex=r"^PO\d{6}$"
                )
                .annotate(purchase_id_int=Cast(Substr("purchase_code", 3), _IntegerField()))
                .aggregate(max_id=Max("purchase_id_int"))
            )["max_id"] or 0
            if value < current_max:
                raise serializers.ValidationError(
                    f"Last purchase ID cannot be lower than the shop's current max purchase number ({current_max})."
                )
        return value

    def get_subscription_status(self, obj):
        if hasattr(obj, "subscription") and obj.subscription:
            return obj.subscription.status
        return obj.subscription_status

    def get_organization(self, obj):
        if obj.organization_id and obj.organization:
            return {
                "id": obj.organization.id,
                "name": obj.organization.name,
                "slug": obj.organization.slug,
                "public_token": obj.organization.public_token,
            }
        return None


class ShopListSerializer(serializers.ModelSerializer):
    subscription_status = serializers.SerializerMethodField()
    subscription_plan = serializers.SerializerMethodField()
    organization = serializers.SerializerMethodField()

    class Meta:
        model = Shop
        fields = [
            "id", "name", "phone", "phone2", "email",
            "img", "place", "subscription_status", "subscription_plan",
            "created_at", "is_active", "organization", "show_prices_in_public_listing",
        ]
        read_only_fields = ["id", "created_at", "organization"]

    def get_subscription_status(self, obj):
        if hasattr(obj, "subscription") and obj.subscription:
            return obj.subscription.status
        return obj.subscription_status

    def get_subscription_plan(self, obj):
        if hasattr(obj, "subscription") and obj.subscription and obj.subscription.plan:
            return {
                "id": obj.subscription.plan.id,
                "name": obj.subscription.plan.name,
                "type": obj.subscription.plan.plan_type,
                "price": str(obj.subscription.plan.base_price),
                "duration_days": obj.subscription.plan.duration_days,
            }
        return None

    def get_organization(self, obj):
        if obj.organization_id and obj.organization:
            return {
                "id": obj.organization.id,
                "name": obj.organization.name,
                "slug": obj.organization.slug,
                "public_token": obj.organization.public_token,
            }
        return None

class ShopUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = [
            "id", "name", "place", "phone", "phone2", "email", "address",
            "gst_number", "city", "state", "pincode",
            "subscription_status", "subscribed_date", "renewal_due_date",
            "img"
        ]
