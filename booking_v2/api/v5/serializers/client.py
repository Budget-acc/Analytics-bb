# booking_v2/api/v5/serializers/client.py
from rest_framework import serializers
from booking_v2.models import Client


class ClientDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = [
            "id",
            "client_name",
            "client_phone_1",
            "client_phone_2",
            "phone_1_e164",
            "phone_2_e164",
        ]
