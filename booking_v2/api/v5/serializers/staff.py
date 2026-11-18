from rest_framework import serializers
from shop.models import Staff


class StaffDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = ["id", "name", "phone", "created_at"]
