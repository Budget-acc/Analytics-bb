from rest_framework import serializers
from finance.models import Account


class AccountDailySummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    account_type = serializers.ChoiceField(
        choices=Account.ACCOUNT_TYPE_CHOICES,
        read_only=True,
    )
    opening_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    closing_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    money_in = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    money_out = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)


class DailySummarySerializer(serializers.Serializer):
    date = serializers.DateField(read_only=True)
    opening_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    closing_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    money_in = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    money_out = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    accounts = AccountDailySummarySerializer(many=True, read_only=True)
