from rest_framework import serializers
from tailored.models import (
    TailorOrder, TailorOrderItem, TailorOrderAdditionalCharge, CustomerPayment, TailorMaterialUsage,
    TailorOrderSecurityDepositAdjustment, TailorOrderItemAssignment,
)


class TailorMaterialUsageSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="material.product.name", read_only=True)
    product_id = serializers.IntegerField(source="material.product.id", read_only=True)
    category = serializers.CharField(source="material.product.category", read_only=True)
    image = serializers.CharField(source="material.product.image", read_only=True)
    variant_id = serializers.IntegerField(source="material.id", read_only=True)
    thumbnail_image = serializers.CharField(source="material.product.thumbnail_image", read_only=True)

    class Meta:
        model = TailorMaterialUsage
        fields = ["id", "name", "product_id", "variant_id", "image", "thumbnail_image", "category", "quantity_meters", "total_amount",]


class TailorOrderItemAssignmentSerializer(serializers.ModelSerializer):
    tailor_id = serializers.IntegerField(source="tailor.id", read_only=True)
    tailor_name = serializers.CharField(source="tailor.name", read_only=True)

    class Meta:
        model = TailorOrderItemAssignment
        fields = ["id", "tailor_id", "tailor_name", "tailor_charge", "tailoring_charge_from_customer",
                  "expected_completion_date", "actual_completion_date", "created_at"]


class TailorOrderItemSerializer(serializers.ModelSerializer):
    assigned_tailor_id = serializers.IntegerField(source="assigned_tailor.id", read_only=True)
    assigned_tailor_name = serializers.CharField(source="assigned_tailor.name", read_only=True)
    materials = TailorMaterialUsageSerializer(source="material_usages", many=True, read_only=True)
    assignments = TailorOrderItemAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = TailorOrderItem
        fields = [
            "id", "product_name", "product_type", "quantity", "reference_image",
            "assigned_tailor_id", "assigned_tailor_name", "status", "tailor_charge", "customer_tailoring_charge",
            "customer_item_charge", "created_product_variant", "expected_completion_date",
            "actual_completion_date", "notes", "measurements", "size", "materials", "assignments",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        measurements = instance.measurements
        if isinstance(measurements, list):
            # Convert back to dict if needed
            data["measurements"] = {m["name"]: m["value"] for m in measurements}
        return data


class TailorOrderAdditionalChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TailorOrderAdditionalCharge
        fields = ["id", "name", "amount", "created_at"]


class CustomerPaymentSerializer(serializers.ModelSerializer):
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = CustomerPayment
        fields = ["id", "payment_amount", "payment_method", "account_id", "account_name", "created_at"]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class TailorOrderListSerializer(serializers.ModelSerializer):
    products = serializers.SerializerMethodField()
    staff_color = serializers.SerializerMethodField()

    class Meta:
        model = TailorOrder
        fields = [
            "id", "shop_tailor_id", "status", "customer_order_type", "created_at", "pickup_date", "products", "staff_color"
        ]

    def get_products(self, obj):
        # Prefetch ensures this won't cause N+1 queries (handled in queryset)
        items = getattr(obj, "prefetched_items", None) or obj.items.all()
        names = [item.product_name for item in items if item.product_name]
        return ", ".join(names)

    def get_staff_color(self, obj):
        return obj.staff.color if obj.staff else None


class TailorOrderDetailSerializer(serializers.ModelSerializer):
    items = TailorOrderItemSerializer(many=True, read_only=True)
    additional_charges = TailorOrderAdditionalChargeSerializer(many=True, read_only=True)
    payments = CustomerPaymentSerializer(many=True, read_only=True)
    staff_id = serializers.IntegerField(source="staff.id", read_only=True)
    staff_name = serializers.CharField(source="staff.name", read_only=True)

    client = serializers.SerializerMethodField()

    class Meta:
        model = TailorOrder
        fields = [
            "id", "shop_tailor_id", "client", "staff_id","staff_name",
            "status", "customer_order_type", "client_phone_number", "client_address",
            "pickup_date", "return_date", "notes",
            "customer_total_charge", "paid_amount", "discount_amount", "security_amount",
            "total_tailor_charges", "total_additional_charges", "created_booking", "documents", "documents_details",
            "items", "additional_charges", "payments", "created_at", "recorded_at"
        ]

    def get_client(self, obj):
        """Return client data without importing Client model"""
        if not obj.client:
            return None

        return {
            "id": obj.client.id,
            "client_name": obj.client.client_name,
            "client_phone_1": obj.client.client_phone_1,
            "client_phone_2": obj.client.client_phone_2,
            "phone_1_e164": str(obj.client.phone_1_e164) if obj.client.phone_1_e164 else None,
            "phone_2_e164": str(obj.client.phone_2_e164) if obj.client.phone_2_e164 else None,
        }


class TailorOrderSecurityAdjustmentSerializer(serializers.ModelSerializer):
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = TailorOrderSecurityDepositAdjustment
        fields = [
            "id", "adjustment_type", "amount", "payment_method",
            "account_id", "account_name", "reason", "created_at",
        ]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None
