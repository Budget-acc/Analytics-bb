from rest_framework import serializers

from finance.models import Account


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = ["id", "name", "account_type", "description", "account_number",
                  "is_default", "is_active", "created_at"]
        read_only_fields = ["id", "is_default", "created_at"]
