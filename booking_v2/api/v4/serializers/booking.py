# booking_v2/api/v4/serializers/booking.py
from rest_framework import serializers
from booking_v2.models import BookingV2, ClientRequirementV2
from booking_v2.api.v4.serializers.client import ClientDetailSerializer


class ClientRequirementV2Serializer(serializers.ModelSerializer):
    class Meta:
        model = ClientRequirementV2
        fields = [
            "id",
            "description",
            "created_at",
        ]


class BookingListSerializer(serializers.ModelSerializer):
    client = serializers.SerializerMethodField()
    booked_items = serializers.SerializerMethodField()
    staff_color = serializers.SerializerMethodField()
    staff_name = serializers.SerializerMethodField()

    class Meta:
        model = BookingV2
        fields = [
            "id",
            "shop_booking_id",
            "client",
            "pickup_date",
            "return_date",
            "cooling_period_end",
            "booking_status",
            "payment_status",
            "staff_name",
            "staff_color",
            "delivery_status",
            "created_at",
            "booked_items",
        ]

    def get_client(self, obj):
        return obj.client_identity.client_name if obj.client_identity else None

    def get_booked_items(self, obj):
        return [item.product_name for item in obj.booked_items.all()]

    def get_staff_color(self, obj):
        return obj.staff.color if obj.staff else None

    def get_staff_name(self, obj):
        return obj.staff.name if obj.staff else None


class BookingDetailSerializer(serializers.ModelSerializer):
    client = ClientDetailSerializer(source="client_identity")

    class Meta:
        model = BookingV2
        fields = [
            "id",
            "shop_booking_id",
            "pickup_date",
            "return_date",
            "cooling_period_end",
            "total_amount",
            "discount_amount",
            "advance_amount",
            "security_amount",
            "purchase_mode",
            "booking_status",
            "payment_status",
            "delivery_status",
            "staff_name",
            "details",
            "created_at",
            "completed_at",
            "client_address",
            "client",
        ]
