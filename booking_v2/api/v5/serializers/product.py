# booking_v2/api/v5/serializers/product.py
from rest_framework import serializers
from services.models import ShopOwnerService
from booking_v2.models import BookedProductVariant
from booking_v2.utils.measurements import from_stored_measurements


class ServiceDetailSerializer(serializers.ModelSerializer):
    general_service_name = serializers.CharField(source="general_service.name")

    class Meta:
        model = ShopOwnerService
        fields = ["id", "general_service_name"]


class BookedProductVariantSerializer(serializers.ModelSerializer):
    main_service_name = serializers.CharField(
        source="product_variant.product.shop_service.general_service.main_category.name",
        read_only=True
    )
    product_id = serializers.SerializerMethodField()
    variant_id = serializers.SerializerMethodField()
    product_image = serializers.CharField(
        source="product_variant.product.image", read_only=True
    )
    service_id = serializers.IntegerField(
        source="product_variant.product.shop_service.id", read_only=True
    )
    attributes = serializers.SerializerMethodField()
    product_code = serializers.CharField(source="product_variant.product.full_product_code", read_only=True)

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
            "product_code",
            "amount",
            "quantity",
            "product_thumbnail",
            "product_image",
            "service_id",
            "main_service_name",
            "attributes",
            "measurements",
            "return_status",
            "returned_at",
            "extended_until",
        ]

    def get_product_id(self, obj):
        variant = getattr(obj, "product_variant", None)
        if not variant:
            return None
        product = getattr(variant, "product", None)
        return getattr(product, "id", None)

    def get_variant_id(self, obj):
        return obj.product_variant_id

    def get_attributes(self, obj):
        """
        Merge product and variant attributes.

        Rule:
        Variant attributes override product attributes if keys collide.
        """
        variant = getattr(obj, "product_variant", None)
        if not variant:
            return {}

        attributes = {}

        product = getattr(variant, "product", None)

        # Product attributes
        if product:
            for attr_value in product.attribute_values.select_related("attribute"):
                key = getattr(attr_value.attribute, "attribute_key", None)
                if key:
                    attributes[key] = attr_value.value

        # Variant attributes (override product attributes)
        for attr_value in variant.attribute_values.select_related("attribute"):
            key = getattr(attr_value.attribute, "attribute_key", None)
            if key:
                attributes[key] = attr_value.value

        return attributes

    def to_representation(self, instance):
        data = super().to_representation(instance)
        measurements = instance.measurements
        if isinstance(measurements, list):
            data["measurements"] = from_stored_measurements(measurements)
        return data
