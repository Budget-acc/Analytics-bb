# booking_v2/api/v5/serializers/booking.py
from rest_framework import serializers
from booking_v2.models import BookingV2, BookingAdditionalCharge
from booking_v2.api.v5.serializers.client import ClientDetailSerializer


class BookingDetailSerializer(serializers.ModelSerializer):
    client = ClientDetailSerializer(source="client_identity")
    staff_name = serializers.SerializerMethodField()
    staff_id = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    class Meta:
        model = BookingV2
        fields = [
            "id",
            "shop_booking_id",
            "pickup_date",
            "pickup_time",
            "return_date",
            "return_time",
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
            "staff_id",
            "details",
            "created_at",
            "completed_at",
            "recorded_at",
            "client_address",
            "client",
            "description",
            "documents",
            "documents_details",
            "rental_duration_type",
        ]

    def get_staff_name(self, obj):
        if obj.staff:
            return obj.staff.name
        return obj.staff_name or None

    def get_staff_id(self, obj):
        if obj.staff:
            return obj.staff.id
        return None

    def get_description(self, obj):
        requirement = getattr(obj, "requirements", None)
        if requirement is None:
            return None
        first_req = requirement.first()
        return getattr(first_req, "description", None)


class BookingAdditionalChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingAdditionalCharge
        fields = ["id", "name", "amount", "created_at"]


class BookingDesktopListSerializer(serializers.ModelSerializer):
    """Optimized serializer for desktop booking list view"""
    client = serializers.SerializerMethodField()
    staff_color = serializers.CharField(source="staff.color", read_only=True, allow_null=True)
    booked_items = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    staff_name = serializers.CharField(source="staff.name", read_only=True, allow_null=True)
    total_amount_after_discount = serializers.SerializerMethodField()


    class Meta:
        model = BookingV2
        fields = [
            "id",
            "shop_booking_id",
            "client",
            "pickup_date",
            "return_date",
            "payment_status",
            "staff_name",
            "staff_color",
            "delivery_status",
            "created_at",
            "completed_at",
            "booked_items",
            "advance_amount",
            "remaining_amount",
            "total_amount_after_discount",
        ]

    def get_client(self, obj):
        """Safely get client name with null handling"""
        if hasattr(obj, 'client_identity') and obj.client_identity:
            return obj.client_identity.client_name or ""
        return ""

    def get_booked_items(self, obj):
        """Return booked items as comma-separated string"""
        if hasattr(obj, 'booked_items_list'):
            # Use prefetched data to avoid N+1 queries
            items = obj.booked_items_list
            if items:
                return ", ".join(filter(None, [item.product_name for item in items]))
        return ""

    def get_remaining_amount(self, obj):
        """Calculate remaining amount with null safety"""
        try:
            total = obj.total_amount or 0
            advance = obj.advance_amount or 0
            discount = obj.discount_amount or 0
            remaining = total - advance - discount
            return max(0, remaining)  # Ensure non-negative
        except (AttributeError, TypeError):
            return 0

    def get_total_amount_after_discount(self, obj):
        """Calculate total amount after discount with null safety"""
        try:
            total = obj.total_amount or 0
            discount = obj.discount_amount or 0
            return max(0, total - discount)  # Ensure non-negative
        except (AttributeError, TypeError):
            return 0
