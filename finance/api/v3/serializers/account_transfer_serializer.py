import logging

from django.utils import timezone
from rest_framework import serializers

logger = logging.getLogger("api_requests")

from finance.models import Account, AccountTransfer, DailyBalance


class AccountTransferWriteSerializer(serializers.Serializer):
    from_account_id = serializers.IntegerField()
    to_account_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    transfer_date_time = serializers.DateTimeField(
        input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S']
    )
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Transfer amount must be greater than zero.")
        return value

    def validate(self, attrs):
        """
        Validates the account transfer request.

        Balance check uses the most recent DailyBalance closing_balance on or
        before today. If no entry exists for today, yesterday's (or older)
        closing balance is used. This means intraday transactions made after the
        last DailyBalance snapshot are not reflected — the check may approve a
        transfer based on a figure that is slightly higher than the real-time
        balance. A warning is logged when stale data is detected.
        """
        shop_id = self.context['shop_id']

        from_account = Account.objects.filter(
            id=attrs['from_account_id'],
            shop_id=shop_id,
            deleted__isnull=True,
        ).first()
        if not from_account:
            raise serializers.ValidationError({"from_account_id": "Source account not found."})

        to_account = Account.objects.filter(
            id=attrs['to_account_id'],
            shop_id=shop_id,
            deleted__isnull=True,
        ).first()
        if not to_account:
            raise serializers.ValidationError({"to_account_id": "Destination account not found."})

        if not from_account.is_active:
            raise serializers.ValidationError({"from_account_id": "Source account is inactive."})

        if not to_account.is_active:
            raise serializers.ValidationError({"to_account_id": "Destination account is inactive."})

        if from_account.id == to_account.id:
            raise serializers.ValidationError(
                {"to_account_id": "Source and destination accounts must be different."}
            )

        # Balance check: latest closing balance on or before today
        today = timezone.localdate()
        latest_balance = (
            DailyBalance.objects
            .filter(
                account=from_account,
                shop_id=shop_id,
                date__lte=today,
                deleted__isnull=True,
            )
            .order_by('-date')
            .first()
        )
        available = latest_balance.closing_balance if latest_balance else 0

        if latest_balance and latest_balance.date < today:
            logger.warning(
                "Account transfer balance check using stale DailyBalance "
                "(account_id=%s, balance_date=%s, today=%s, available=%s)",
                from_account.id, latest_balance.date, today, available,
            )

        if attrs['amount'] > available:
            raise serializers.ValidationError(
                {"amount": f"Insufficient balance in source account. Available: {available}."}
            )

        attrs['from_account'] = from_account
        attrs['to_account'] = to_account
        return attrs


class AccountTransferReadSerializer(serializers.ModelSerializer):
    from_account_id = serializers.IntegerField(source='from_account.id', read_only=True)
    from_account_name = serializers.CharField(source='from_account.name', read_only=True)
    to_account_id = serializers.IntegerField(source='to_account.id', read_only=True)
    to_account_name = serializers.CharField(source='to_account.name', read_only=True)
    created_by_id = serializers.IntegerField(source='created_by.id', read_only=True, allow_null=True)

    class Meta:
        model = AccountTransfer
        fields = [
            'id',
            'from_account_id',
            'from_account_name',
            'to_account_id',
            'to_account_name',
            'amount',
            'transfer_date_time',
            'notes',
            'created_by_id',
            'created_at',
        ]
