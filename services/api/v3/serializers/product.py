from rest_framework import serializers
from services.models import ShopOwnerService, ProductVariant, Product, ProductTransferHistory
import base64, uuid, boto3, logging
from django.core.files.base import ContentFile
from django.conf import settings
from django.db import transaction, IntegrityError


class ProductVariantSerializer(serializers.ModelSerializer):
    fabric_length = serializers.FloatField(allow_null=True)
    # Number like fabric_length (the model DecimalField would serialize as a string).
    unavailable_fabric_length = serializers.FloatField(read_only=True)
    unique_code = serializers.CharField(read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "attribute", "stock", "fabric_length", "unique_code", "external_qr_code", "unavailable_stock", "unavailable_fabric_length"]

    def validate_attribute(self, value):
        if not value:
            raise serializers.ValidationError("Variant 'attribute' cannot be empty.")
        return value

    # Auto-generate unique_code after creation
    def create(self, validated_data):
        variant = super().create(validated_data)
        self._ensure_unique_code_safe(variant)
        return variant

    # Handle missing code on GET
    def to_representation(self, instance):
        if not instance.unique_code:
            self._ensure_unique_code_safe(instance)
        return super().to_representation(instance)

    @staticmethod
    def _generate_unique_code(variant):
        """Generate deterministic unique code."""
        return f"S{variant.product.shop_service.id}-P{variant.product.id}-V{variant.id}"

    @classmethod
    def _ensure_unique_code_safe(cls, variant):
        """Ensure unique_code exists, using safe transaction with retry."""
        if variant.unique_code:
            return variant

        for _ in range(3):
            try:
                with transaction.atomic():
                    # Lock the row for update, avoiding concurrent modifications
                    variant_locked = (
                        ProductVariant.objects.select_for_update()
                        .filter(pk=variant.pk)
                        .first()
                    )
                    if not variant_locked:
                        return variant

                    if not variant_locked.unique_code:
                        variant_locked.unique_code = cls._generate_unique_code(variant_locked)
                        variant_locked.save(update_fields=["unique_code"])
                    return variant_locked

            except IntegrityError:
                continue
        return variant


class ProductSerializer(serializers.ModelSerializer):
    # variants = ProductVariantSerializer(many=True, required=False)
    variants = serializers.SerializerMethodField()
    attributes = serializers.SerializerMethodField()
    shop_service_id = serializers.IntegerField(write_only=True, required=True)
    # Product.product_code stores only the zero-padded digits (e.g. "00001").
    # The API exposes the combined, human-readable value instead (e.g.
    # "PRD00001"), assembled from the shop's ShopNumberingSequence prefix -
    # see Product.full_product_code.
    product_code = serializers.CharField(source="full_product_code", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "shop_service_id",
            "name",
            "purchase_price",
            "price",
            "sale_price",
            "description",
            "color",
            "category",
            "model",
            "product_code",
            "image",
            "thumbnail_image",
            "attributes",
            "hourly_rental_price",
            "created_at",
            "variants",
        ]
        read_only_fields = ["image", "thumbnail_image"]

    def get_variants(self, obj):
        """
        Return variants. If `in_stock_only=true` was passed in the request,
        exclude variants with stock = 0 or inactive ones.
        """
        request = self.context.get("request")
        in_stock_only = False
        if request:
            in_stock_only = request.query_params.get("in_stock_only", "false").lower() == "true"

        variants_qs = obj.variants.filter(is_active=True).order_by("id")
        if in_stock_only:
            variants_qs = variants_qs.filter(stock__gt=0)

        return ProductVariantSerializer(variants_qs, many=True).data

    def get_attributes(self, obj):
        """
        Return product service attributes as a dictionary.
        with prefetch_related in the view.
        """
        # Check if attributes are prefetched (optimized path)
        if hasattr(obj, '_prefetched_objects_cache') and 'attribute_values' in obj._prefetched_objects_cache:
            attribute_values = obj.attribute_values.all()
        else:
            # Fallback: direct query with select_related
            attribute_values = obj.attribute_values.select_related('attribute').all()

        # Build the attributes dictionary
        attributes_dict = {}
        for attr_value in attribute_values:
            attributes_dict[attr_value.attribute.attribute_key] = attr_value.value

        return attributes_dict

    def validate_variants(self, value):
        if not value:
            raise serializers.ValidationError("At least one variant is required.")
        attributes = [v.get("attribute", "").strip().lower() for v in value]
        if len(attributes) != len(set(attributes)):
            raise serializers.ValidationError(
                "Duplicate variant attributes are not allowed."
            )

        # Capitalize first letter of each variant's attribute
        for v in value:
            if "attribute" in v and v["attribute"]:
                v["attribute"] = v["attribute"].strip().capitalize()

        return value

    def validate_shop_service_id(self, value):
        if self.instance:
            raise serializers.ValidationError("Cannot update shop_service_id.")
        return value

    def to_internal_value(self, data):
        """
        Adjust capitalization rules:
        - Name: First letter uppercase, rest exactly as provided
        - Color/Category/Model: Capitalize first letter and lowercase the rest
        """
        if "name" in data and isinstance(data["name"], str):
            val = data["name"].strip()
            if val:
                data["name"] = val[0].upper() + val[1:]  # keep rest as is

        fields_to_capitalize = ['color', 'category', 'model']
        for field in fields_to_capitalize:
            val = data.get(field)
            if isinstance(val, str):
                data[field] = val.strip().capitalize()

        return super().to_internal_value(data)

    def create(self, validated_data):
        variants_data = validated_data.pop("variants", [])
        shop_service_id = validated_data.pop("shop_service_id")
        shop_service = ShopOwnerService.objects.get(id=shop_service_id)

        product = Product.objects.create(
            shop_service=shop_service,
            **validated_data
        )

        for variant_data in variants_data:
            attribute = variant_data.get("attribute", "").strip()
            if product.variants.filter(attribute__iexact=attribute).exists():
                raise serializers.ValidationError(
                    f"Duplicate variant attribute '{attribute}' (case-insensitive)"
                )
            ProductVariant.objects.create(product=product, **variant_data)

        return product

    def update(self, instance, validated_data):
        variants_data = validated_data.pop("variants", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if variants_data:
            instance.variants.all().delete()
            for variant_data in variants_data:
                ProductVariant.objects.create(product=instance, **variant_data)

        return instance


class ProductVariantViewSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ["id", "product", "attribute", "price", "stock", "fabric_length", "external_qr_code"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        # For create, product comes from attrs; for update, from instance
        product = attrs.get("product") or getattr(self.instance, "product", None)
        attribute = attrs.get("attribute") or getattr(self.instance, "attribute", None)
        if attribute is not None:
            attribute_norm = attribute.strip().lower()
            qs = ProductVariant.objects.filter(
                product=product,
                attribute__iexact=attribute_norm,
                deleted__isnull=True
            )
            # Exclude self in update
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    "attribute": "A variant with this attribute already exists for this product."
                })
        return attrs


class ProductWithMainServiceSerializer(ProductSerializer):
    main_service_name = serializers.SerializerMethodField()
    general_service_name = serializers.CharField(read_only=True)

    class Meta(ProductSerializer.Meta):
        fields = ProductSerializer.Meta.fields + ["main_service_name", "general_service_name"]

    def get_main_service_name(self, obj):
        try:
            main_category = (
                obj.shop_service.general_service.main_category
                if obj.shop_service and obj.shop_service.general_service
                else None
            )
            if main_category:
                return  main_category.name
        except Exception:
            pass
        return None


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
