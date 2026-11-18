# booking_v2/api/v3/serializers/product.py
from rest_framework import serializers
from services.models import ShopOwnerService, Product, ProductVariant
from booking_v2.models import BookedProductVariant
from booking_v2.api.v3.serializers.fields import PreserveOrderJSONField


class BookedProductVariantSerializer(serializers.ModelSerializer):
    main_service_name = serializers.CharField(
        source="product_variant.product.shop_service.general_service.main_category.name",
        read_only=True
    )
    product_id = serializers.SerializerMethodField()
    variant_id = serializers.SerializerMethodField()

    class Meta:
        model = BookedProductVariant
        fields = [
            "id",
            "product_id",
            "variant_id",
            "product_name",
            "variant_attribute",
            "color",
            "category",
            "model",
            "amount",
            "quantity",
            "product_thumbnail",
            "main_service_name",
            "measurements",
        ]

    def get_product_id(self, obj):
        if not obj.product_variant_id:
            return None
        try:
            variant = ProductVariant.all_objects.get(id=obj.product_variant_id)
            return variant.product.id
        except ProductVariant.DoesNotExist:
            return None

    def get_variant_id(self, obj):
        return obj.product_variant_id

    def to_representation(self, instance):
        data = super().to_representation(instance)
        measurements = instance.measurements
        if isinstance(measurements, list):
            data["measurements"] = {m["name"]: m["value"] for m in measurements}
        return data


class ServiceDetailSerializer(serializers.ModelSerializer):
    general_service_name = serializers.CharField(source="general_service.name")

    class Meta:
        model = ShopOwnerService
        fields = ["id", "general_service_name"]


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ["id", "attribute", "price", "stock"]


class ProductWithVariantsSerializer(serializers.ModelSerializer):
    variants = ProductVariantSerializer(many=True)

    class Meta:
        model = Product
        fields = ["id", "name", "color", "category", "model", "image", "variants"]
