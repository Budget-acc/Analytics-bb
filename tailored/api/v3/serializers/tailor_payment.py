from rest_framework import serializers

from tailored.models import TailorPayment


class TailorPaymentWriteSerializer(serializers.ModelSerializer):
    transaction_date_time = serializers.DateTimeField(
        input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S']
    )

    class Meta:
        model = TailorPayment
        fields = [
            'shop',
            'tailor',
            'created_by',
            'transaction_date_time',
            'amount',
            'account',
            'payment_method',
            'category',
            'notes',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be a positive value.")
        return value


class TailorPaymentReadSerializer(serializers.ModelSerializer):
    tailor_id = serializers.IntegerField(source='tailor.id', read_only=True, allow_null=True)
    tailor_name = serializers.CharField(source='tailor.name', read_only=True, allow_null=True)
    account_id = serializers.IntegerField(source='account.id', read_only=True, allow_null=True)
    account_name = serializers.CharField(source='account.name', read_only=True, allow_null=True)
    category_id = serializers.IntegerField(source='category.id', read_only=True, allow_null=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)

    class Meta:
        model = TailorPayment
        fields = [
            'id',
            'tailor_id',
            'tailor_name',
            'transaction_date_time',
            'amount',
            'payment_method',
            'account_id',
            'account_name',
            'category_id',
            'category_name',
            'notes',
            'created_at',
        ]
