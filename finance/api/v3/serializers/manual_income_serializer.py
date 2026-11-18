from rest_framework import serializers

from finance.models import ManualIncome


class ManualIncomeWriteSerializer(serializers.ModelSerializer):
    transaction_date_time = serializers.DateTimeField(
        input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S']
    )

    class Meta:
        model = ManualIncome
        fields = [
            'shop',
            'created_by',
            'transaction_date_time',
            'amount',
            'description',
            'account',
            'payment_method',
            'category',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be a positive value.")
        return value


class ManualIncomeReadSerializer(serializers.ModelSerializer):
    account_id = serializers.IntegerField(source='account.id', read_only=True, allow_null=True)
    account_name = serializers.CharField(source='account.name', read_only=True, allow_null=True)
    category_id = serializers.IntegerField(source='category.id', read_only=True, allow_null=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)

    class Meta:
        model = ManualIncome
        fields = [
            'id',
            'transaction_date_time',
            'amount',
            'description',
            'payment_method',
            'account_id',
            'account_name',
            'category_id',
            'category_name',
            'created_at',
        ]
