from django.utils import timezone
from rest_framework import serializers

from finance.models import LedgerEntry

DIRECTION_LABELS = {"in": "credit", "out": "debit"}


class LedgerEntryHistorySerializer(serializers.ModelSerializer):
    date = serializers.SerializerMethodField()
    transaction_type_display = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()
    balance_after = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = LedgerEntry
        fields = [
            "id", "date", "transaction_type", "transaction_type_display",
            "direction", "amount", "balance_after", "reference_id",
        ]

    def get_date(self, obj):
        return timezone.localtime(obj.created_at).strftime("%d-%m-%Y %H:%M:%S")

    def get_transaction_type_display(self, obj):
        return dict(LedgerEntry.TRANSACTION_TYPE_CHOICES).get(obj.transaction_type, obj.transaction_type)

    def get_direction(self, obj):
        return DIRECTION_LABELS.get(obj.direction, obj.direction)
