from rest_framework import serializers
from services.models import GeneralService, ShopOwnerService, MainServiceCategory
import base64, uuid, boto3, logging
from django.core.files.base import ContentFile
from django.conf import settings
from django.db import transaction, IntegrityError


class GeneralServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneralService
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at"]


class GeneralServiceListSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneralService
        fields = ["id", "name", "description", "icon", "is_active"]


class MainServiceCategorySerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(default=True)
    class Meta:
        model = MainServiceCategory
        fields = '__all__'
        read_only_fields = ['created_at']


class ShopOwnerServiceSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="general_service.name", read_only=True)
    description = serializers.CharField(source="general_service.description", read_only=True)
    icon = serializers.URLField(source="general_service.icon", read_only=True)
    main_service_name = serializers.CharField(
        source="general_service.main_category.name", read_only=True
    )

    class Meta:
        model = ShopOwnerService
        fields = [
            "id",
            "service_name",
            "description",
            "icon",
            "main_service_name",
            "display_order",
        ]
