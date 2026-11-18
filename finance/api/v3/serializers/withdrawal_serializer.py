from rest_framework import serializers

from finance.models import Withdrawal


class WithdrawalReadSerializer(serializers.ModelSerializer):
    account_id = serializers.IntegerField(source='account.id', read_only=True, allow_null=True)
    account_name = serializers.CharField(source='account.name', read_only=True, allow_null=True)
    category_id = serializers.IntegerField(source='category.id', read_only=True, allow_null=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)

    class Meta:
        model = Withdrawal
        fields = [
            'id',
            'account_id',
            'account_name',
            'withdrawn_by_display_name',
            'withdrawn_by_phone',
            'withdrawn_by_email',
            'withdrawal_reason',
            'purpose',
            'payment_method',
            'category_id',
            'category_name',
            'amount',
            'transaction_date',
            'reference_number',
            'notes',
            'status',
            'is_opening_entry',
            'created_at',
        ]
