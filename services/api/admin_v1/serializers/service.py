from rest_framework import serializers
from services.models import GeneralService, ShopOwnerService, ProductVariant, Product, MainServiceCategory, ProductTransferHistory
import base64, uuid, boto3, logging
from django.core.files.base import ContentFile
from django.conf import settings


class GeneralServiceSerializer(serializers.ModelSerializer):
    main_category_name = serializers.SerializerMethodField()

    class Meta:
        model = GeneralService
        fields = ["id", "name", "description", "icon", "is_active", "created_at", "updated_at", "main_category", "main_category_name"]
        read_only_fields = ["created_at", "updated_at"]

    def get_main_category_name(self, obj):
        if obj.main_category:
            return obj.main_category.name
        return None

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
        ]


class ProductTransferHistorySerializer(serializers.ModelSerializer):
    source_shop_name = serializers.CharField(source='source_shop.name', read_only=True)
    destination_shop_name = serializers.CharField(source='destination_shop.name', read_only=True)
    source_product_name = serializers.CharField(source='source_product.name', read_only=True)
    destination_product_name = serializers.CharField(source='destination_product.name', read_only=True)
    initiated_by_name = serializers.CharField(source='initiated_by.full_name', read_only=True)
    source_variant_attribute = serializers.CharField(source='source_variant.attribute', read_only=True)
    destination_variant_attribute = serializers.CharField(source='destination_variant.attribute', read_only=True)

    transfer_mode = serializers.SerializerMethodField()

    product = serializers.SerializerMethodField()
    source_shop_details = serializers.SerializerMethodField()
    destination_shop_details = serializers.SerializerMethodField()

    class Meta:
        model = ProductTransferHistory
        fields = [
            'id', 'transfer_id', 'status', 'quantity_transferred', 'transfer_type',
            'transfer_date', 'source_shop_name', 'destination_shop_name',
            'source_product_name', 'destination_product_name', 'initiated_by_name',
            'source_variant_attribute', 'destination_variant_attribute', 'transfer_mode',
            'product', 'source_shop_details', 'destination_shop_details'
        ]

    def get_transfer_mode(self, obj):
        request = self.context.get('request')
        shop_id = request.query_params.get('shop_id') if request else None

        if shop_id is None:
            return None

        return 'outgoing' if str(obj.source_shop_id) == str(shop_id) else 'incoming'

    def get_product(self, obj):
        # Prefer source_product info as primary (can adjust logic based on transfer_mode if needed)
        product = obj.source_product

        return {
            "id": product.id,
            "name": product.name,
            "model": product.model,
            "category": product.category,
            "color": product.color,
            "price": product.price,
            "image": product.thumbnail_image,
            # "thumbnail_image": product.thumbnail_image,
        }

    def get_source_shop_details(self, obj):
        if not obj.source_shop:
            return None
        return {
            "place": obj.source_shop.place,
            "address": obj.source_shop.address,
        }

    def get_destination_shop_details(self, obj):
        if not obj.destination_shop:
            return None
        return {
            "place": obj.destination_shop.place,
            "address": obj.destination_shop.address,
        }
