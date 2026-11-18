
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from marketplace.models.marketplace_product import (
    MarketplaceProduct,
)
from marketplace.models.marketplace_product_fabric import (
    MarketplaceProductFabric,
)
from marketplace.models.fabric import Fabric


class ProductFabricInputSerializer(serializers.Serializer):
    fabric = serializers.PrimaryKeyRelatedField(
        queryset=Fabric.all_objects.all(),
        required=True,
    )

    selling_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        min_value=Decimal("0.01"),
    )


class MarketplaceProductSerializer(serializers.ModelSerializer):

    # Write format:
    # "fabrics": [
    #   {"fabric": 1, "selling_price": "1250.00"}
    # ]
    fabrics = ProductFabricInputSerializer(
        many=True,
        required=True,
        write_only=True,
    )

    sub_category_name = serializers.CharField(
        source="subcategory.name",
        read_only=True,
    )

    category_name = serializers.CharField(
        source="subcategory.category.name",
        read_only=True,
    )

    class Meta:
        model = MarketplaceProduct

        fields = [
            "id",
            "product_code",
            "name",
            "subcategory",
            "sub_category_name",
            "category_name",
            "fabrics",
            "is_premium",
            "color",
            "model",
            "description",
            "images",
            "videos",
            "size_options",
            "minimum_order_quantity",
            "maximum_order_quantity",
            "is_public",
            "show_selling_price",
            "is_active",
            "published_at",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "product_code",
            "images",
            "videos",
            "published_at",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)

        # Read format includes fabric ID, name, and price.
        data["fabrics"] = self.get_fabrics(instance)

        return data

    def get_fabrics(self, obj):
        relationships = [
            relationship
            for relationship in obj.product_fabrics.all()
            if relationship.is_active
        ]

        return [
            {
                "id": relationship.fabric_id,
                "name": relationship.fabric.name,
                "selling_price": str(relationship.selling_price),
            }
            for relationship in relationships
        ]

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Product name cannot be empty."
            )

        return value

    def validate_subcategory(self, value):
        # Allow an existing product to retain its current subcategory,
        # even if the subcategory or category has since been deactivated.
        if (
            self.instance is not None
            and value.pk == self.instance.subcategory_id
        ):
            return value

        if not value.is_active:
            raise serializers.ValidationError(
                "Cannot assign a product to an inactive subcategory."
            )

        if not value.category.is_active:
            raise serializers.ValidationError(
                "Cannot assign a product to a subcategory "
                "whose category is inactive."
            )

        return value

    

    def validate_minimum_order_quantity(self, value):
        if value < 1:
            raise serializers.ValidationError(
                "Minimum order quantity must be at least 1."
            )
        return value

    def validate(self, attrs):
        minimum_quantity = attrs.get(
            "minimum_order_quantity",
            getattr(self.instance, "minimum_order_quantity", 1),
        )

        maximum_quantity = attrs.get(
            "maximum_order_quantity",
            getattr(self.instance, "maximum_order_quantity", None),
        )

        if (
            maximum_quantity is not None
            and maximum_quantity < minimum_quantity
        ):
            raise serializers.ValidationError({
                "maximum_order_quantity": (
                    "Maximum order quantity cannot be less "
                    "than minimum order quantity."
                )
            })

        fabrics = attrs.get("fabrics")

        if fabrics is not None:
            existing_fabric_ids = set()

            if self.instance is not None:
                existing_fabric_ids = set(
                    MarketplaceProductFabric.all_objects.filter(
                        product=self.instance,
                    ).values_list("fabric_id", flat=True)
                )

            for item in fabrics:
                fabric = item["fabric"]

                if not fabric.is_active and fabric.id not in existing_fabric_ids:
                    raise serializers.ValidationError({
                        "fabrics": (
                            f"Fabric '{fabric.name}' is inactive and "
                            "cannot be newly assigned to a product."
                        )
                    })

        elif self.instance is None:
            raise serializers.ValidationError({
                "fabrics": "At least one fabric is required."
            })

        elif not self.instance.product_fabrics.filter(
            is_active=True
        ).exists():
            raise serializers.ValidationError({
                "fabrics": (
                    "At least one fabric with a selling price is required."
                )
            })

        is_public = attrs.get(
            "is_public",
            getattr(self.instance, "is_public", False),
        )

        is_active = attrs.get(
            "is_active",
            getattr(self.instance, "is_active", True),
        )

        if is_public and not is_active:
            raise serializers.ValidationError({
                "is_public": "An inactive product cannot be made public."
            })

        return attrs

    def create(self, validated_data):
        fabrics = validated_data.pop("fabrics")

        if (
            validated_data.get("is_public", False)
            and validated_data.get("is_active", True)
        ):
            validated_data["published_at"] = timezone.now()

        product = MarketplaceProduct.objects.create(
            product_code=self.generate_product_code(),
            **validated_data,
        )

        self.sync_fabrics(product, fabrics)

        return product

    def update(self, instance, validated_data):
        fabrics_provided = "fabrics" in validated_data
        fabrics = validated_data.pop("fabrics", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if (
            instance.is_public
            and instance.is_active
            and instance.published_at is None
        ):
            instance.published_at = timezone.now()

        instance.save()

        if fabrics_provided:
            self.sync_fabrics(instance, fabrics)

        return instance

    def sync_fabrics(self, product, fabrics):
        selected_fabric_ids = {
            item["fabric"].id for item in fabrics
        }

        existing_relationships = (
            MarketplaceProductFabric.all_objects.filter(
                product=product,
            )
        )

        for item in fabrics:
            fabric = item["fabric"]
            selling_price = item["selling_price"]

            relationship = (
                MarketplaceProductFabric.all_objects.filter(
                    product=product,
                    fabric=fabric,
                ).first()
            )

            if relationship:
                if relationship.deleted:
                    relationship.undelete()

                relationship.is_active = True
                relationship.selling_price = selling_price

                relationship.save(
                    update_fields=[
                        "is_active",
                        "selling_price",
                        "updated_at",
                    ]
                )
            else:
                MarketplaceProductFabric.objects.create(
                    product=product,
                    fabric=fabric,
                    selling_price=selling_price,
                    is_active=True,
                )

        # Deactivate fabric relationships omitted from the update.
        existing_relationships.exclude(
            fabric_id__in=selected_fabric_ids,
        ).update(is_active=False)

    def generate_product_code(self):
        last_product = (
            MarketplaceProduct.all_objects.filter(
                product_code__startswith="MKT"
            )
            .order_by("-id")
            .first()
        )

        if not last_product:
            next_number = 1
        else:
            try:
                last_number = int(
                    last_product.product_code.replace("MKT", "", 1)
                )
                next_number = last_number + 1
            except (ValueError, TypeError):
                next_number = 1

        return f"MKT{next_number:06d}"
