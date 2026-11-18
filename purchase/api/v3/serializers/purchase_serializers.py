from rest_framework import serializers

from ....models import Purchase, PurchaseItem, PurchasePayment


class PurchaseItemSerializer(serializers.ModelSerializer):
    """Read-only serializer for purchase line items."""

    class Meta:
        model = PurchaseItem
        fields = [
            'id',
            'product_variant_id',
            'product_name',
            'variant_size',
            'variant_color',
            'variant_other',
            'quantity',
            'purchase_amount',
            'line_total',
            'stock_increased',
        ]
        read_only_fields = fields


class PurchasePaymentSerializer(serializers.ModelSerializer):
    """Read-only serializer for purchase payments."""

    account_name = serializers.CharField(source='account.name', read_only=True)

    class Meta:
        model = PurchasePayment
        fields = [
            'id',
            'amount',
            'account_id',
            'account_name',
            'payment_method',
            'payment_date',
            'transaction_reference',
            'notes',
            'created_at',
        ]
        read_only_fields = fields


class PurchaseListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing purchases (also used by VendorViewSet.purchases)."""

    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    outstanding_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Purchase
        fields = [
            'id',
            'purchase_code',
            'vendor_name',
            'purchase_type',
            'purchase_date',
            'grand_total',
            'paid_amount',
            'outstanding_balance',
            'is_fully_paid',
            'is_completed',
            'created_at',
        ]
        read_only_fields = fields


class PurchaseDetailSerializer(serializers.ModelSerializer):
    """Detailed, read-only serializer for retrieving a single purchase."""

    vendor = serializers.SerializerMethodField()
    items = PurchaseItemSerializer(many=True, read_only=True)
    payments = PurchasePaymentSerializer(many=True, read_only=True)
    outstanding_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Purchase
        fields = [
            'id',
            'purchase_code',
            'vendor',
            'purchase_type',
            'purchase_date',
            'invoice_number',
            'tax_rate',
            'tax_amount',
            'purchase_total',
            'total_quantity',
            'additional_charges_total',
            'grand_total',
            'paid_amount',
            'outstanding_balance',
            'is_fully_paid',
            'is_completed',
            'notes',
            'documents',
            'items',
            'payments',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_vendor(self, obj):
        if obj.vendor:
            return {
                'id': obj.vendor.id,
                'name': obj.vendor.name,
                'contact_number': str(obj.vendor.contact_number) if obj.vendor.contact_number else None,
                'email': obj.vendor.email,
            }
        return None
