

from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from marketplace.models.marketplace_product import MarketplaceProduct
from marketplace.models.marketplace_product_fabric import MarketplaceProductFabric
from marketplace.models.fabric import Fabric


class FabricSerializer(serializers.ModelSerializer):
    """Fabric details with product-specific selling price."""

    category = serializers.CharField(
        source="category.name",
        read_only=True,
        allow_null=True,
    )

    selling_price = serializers.SerializerMethodField()

    class Meta:
        model = Fabric
        fields = [
            "id",
            "name",
            "category",
            "selling_price",
        ]

    def get_selling_price(self, obj):
        """
        The selling price belongs to MarketplaceProductFabric,
        not to Fabric itself.
        """
        relationship_prices = self.context.get(
            "fabric_prices",
            {},
        )

        price = relationship_prices.get(obj.id)

        if price is None:
            return None

        show_price = self.context.get(
            "show_selling_price",
            True,
        )

        return str(price) if show_price else None


class MarketplaceProductListSerializer(serializers.ModelSerializer):
    """
    Client-facing product list serializer.
    Returns minimal product information and one thumbnail image.
    Selling prices belong to individual product-fabric relationships.
    """

    category_name = serializers.CharField(
        source="subcategory.category.name",
        read_only=True,
    )
    category_id = serializers.IntegerField(
        source="subcategory.category.id",
        read_only=True,
    )
    subcategory_name = serializers.CharField(
        source="subcategory.name",
        read_only=True,
    )
    subcategory_id = serializers.IntegerField(
        source="subcategory.id",
        read_only=True,
    )

    thumbnail = serializers.SerializerMethodField()
    fabrics = serializers.SerializerMethodField()
    fabrics_count = serializers.SerializerMethodField()

    class Meta:
        model = MarketplaceProduct
        fields = [
            "id",
            "product_code",
            "name",
            "category_id",
            "category_name",
            "subcategory_id",
            "subcategory_name",
            "is_premium",
            "color",
            "model",
            "description",
            "thumbnail",
            "show_selling_price",
            "minimum_order_quantity",
            "maximum_order_quantity",
            "fabrics",
            "fabrics_count",
        ]

    def get_thumbnail(self, obj):
        """Return the first image with the lowest sort_order."""
        if not obj.images:
            return None

        sorted_images = sorted(
            obj.images,
            key=lambda image: image.get("sort_order", 999),
        )

        image = sorted_images[0]

        return {
            "url": image.get("url"),
            "thumbnail_url": image.get(
                "thumbnail_url",
                image.get("url"),
            ),
            "alt": image.get("alt", obj.name),
        }

    def get_fabrics_count(self, obj):
        """
        Total active fabric relationships for the product,
        regardless of the filters selected for this listing.
        """
        return sum(
            1
            for relationship in obj.product_fabrics.all()
            if relationship.is_active and relationship.fabric.is_active
        )


    def get_fabrics(self, obj):
        """
        Return only eligible fabric relationships for this listing.
        Keep the total fabric count independent of these filters.
        """
        relationships = [
            relationship
            for relationship in obj.product_fabrics.all()
            if relationship.is_active and relationship.fabric.is_active
        ]

        request = self.context.get("request")
        filters = request.data if request and request.method == "POST" else {}

        # 1. Filter by selected fabric IDs.
        fabric_ids = filters.get("fabric_ids") or []
        if fabric_ids:
            fabric_ids = {int(value) for value in fabric_ids}
            relationships = [
                relationship
                for relationship in relationships
                if relationship.fabric_id in fabric_ids
            ]

        # 2. Filter by the requested price range.
        min_price = filters.get("min_price")
        max_price = filters.get("max_price")

        if min_price is not None:
            min_price = Decimal(str(min_price))
            relationships = [
                relationship
                for relationship in relationships
                if relationship.selling_price >= min_price
            ]

        if max_price is not None:
            max_price = Decimal(str(max_price))
            relationships = [
                relationship
                for relationship in relationships
                if relationship.selling_price <= max_price
            ]

        # 3. When sorting by price, show the fabric responsible
        # for that product's displayed lowest/highest price.
        sort_by = filters.get("sort_by", "newest")

        if relationships and sort_by == "price_low_high":
            relationships = [
                min(relationships, key=lambda item: item.selling_price)
            ]
        elif relationships and sort_by == "price_high_low":
            relationships = [
                max(relationships, key=lambda item: item.selling_price)
            ]

        # 4. Return the fabric ID, name and its own selling price.
        fabric_prices = {
            relationship.fabric_id: relationship.selling_price
            for relationship in relationships
        }

        fabrics = [relationship.fabric for relationship in relationships]

        return FabricSerializer(
            fabrics,
            many=True,
            context={
                "fabric_prices": fabric_prices,
                "show_selling_price": obj.show_selling_price,
            },
        ).data


class MarketplaceProductDetailSerializer(serializers.ModelSerializer):
    """
    Client-facing product detail serializer.
    Returns complete product information with all images, videos,
    and individual fabric selling prices.
    """

    category_name = serializers.CharField(
        source="subcategory.category.name",
        read_only=True,
    )
    category_id = serializers.IntegerField(
        source="subcategory.category.id",
        read_only=True,
    )
    subcategory_name = serializers.CharField(
        source="subcategory.name",
        read_only=True,
    )
    subcategory_id = serializers.IntegerField(
        source="subcategory.id",
        read_only=True,
    )

    images = serializers.SerializerMethodField()
    videos = serializers.SerializerMethodField()
    fabrics = serializers.SerializerMethodField()

    class Meta:
        model = MarketplaceProduct
        fields = [
            "id",
            "product_code",
            "name",
            "category_id",
            "category_name",
            "subcategory_id",
            "subcategory_name",
            "is_premium",
            "color",
            "model",
            "description",
            "images",
            "videos",
            "show_selling_price",
            "size_options",
            "minimum_order_quantity",
            "maximum_order_quantity",
            "fabrics",
            "published_at",
        ]

    def get_images(self, obj):
        """Return all images sorted by sort_order."""
        if not obj.images:
            return []

        sorted_images = sorted(
            obj.images,
            key=lambda image: image.get("sort_order", 999),
        )

        return [
            {
                "url": image.get("url"),
                "thumbnail_url": image.get(
                    "thumbnail_url",
                    image.get("url"),
                ),
                "alt": image.get("alt", obj.name),
                "sort_order": image.get("sort_order", 0),
            }
            for image in sorted_images
        ]

    def get_videos(self, obj):
        """Return all videos sorted by sort_order."""
        if not obj.videos:
            return []

        sorted_videos = sorted(
            obj.videos,
            key=lambda video: video.get("sort_order", 999),
        )

        return [
            {
                "url": video.get("url"),
                "thumbnail_url": video.get("thumbnail_url", ""),
                "title": video.get("title", ""),
                "provider": video.get("provider", "s3"),
                "sort_order": video.get("sort_order", 0),
            }
            for video in sorted_videos
        ]

    def get_fabrics(self, obj):
        """Return active fabrics with their individual selling prices."""

        relationships = [
            relationship
            for relationship in obj.product_fabrics.all()
            if relationship.is_active
        ]

        fabric_prices = {
            relationship.fabric_id: relationship.selling_price
            for relationship in relationships
        }

        fabrics = [
            relationship.fabric
            for relationship in relationships
        ]

        return FabricSerializer(
            fabrics,
            many=True,
            context={
                "fabric_prices": fabric_prices,
                "show_selling_price": obj.show_selling_price,
            },
        ).data


class ProductFilterSerializer(serializers.Serializer):
    """
    Serializer for validating product filter POST body.
    Uses category names instead of IDs.
    """

    categories = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=True,
        min_length=1,
        help_text=(
            "List of category names (at least one required). "
            "Case-insensitive."
        ),
    )

    models = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        allow_empty=True,
        help_text="Optional list of model names (case-insensitive)",
    )

    subcategory_ids = serializers.ListField(
    child=serializers.IntegerField(min_value=1),
    required=False,
    allow_empty=False,
    help_text="Optional list of subcategory IDs",
    )

    fabric_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
        help_text="Optional list of fabric IDs",
    )

    is_premium = serializers.BooleanField(
        required=False,
        allow_null=True,
        help_text="Filter by premium status (true/false/null)",
    )

    colors = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        allow_empty=True,
        help_text="Optional list of color names (case-insensitive)",
    )

    show_selling_price = serializers.BooleanField(
        required=False,
        allow_null=True,
        help_text="Filter products by selling-price visibility",
    )

    min_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        help_text="Minimum fabric selling price",
    )

    max_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        help_text="Maximum fabric selling price",
    )

    search = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text=(
            "Search by product name, code, description, "
            "subcategory name, or fabric name"
        ),
    )

    sort_by = serializers.ChoiceField(
        choices=[
            "newest",
            "oldest",
            "price_low_high",
            "price_high_low",
            "name_asc",
            "name_desc",
        ],
        default="newest",
        required=False,
        help_text="Sort order for results",
    )

    def validate_categories(self, value):
        cleaned_categories = []

        for category in value:
            cleaned = category.strip()

            if not cleaned:
                raise serializers.ValidationError(
                    "Category names cannot be empty."
                )

            cleaned_categories.append(cleaned)

        return cleaned_categories

    def validate(self, attrs):
        min_price = attrs.get("min_price")
        max_price = attrs.get("max_price")

        if (
            min_price is not None
            and max_price is not None
            and max_price < min_price
        ):
            raise serializers.ValidationError({
                "max_price": (
                    "Maximum price must be greater than or equal "
                    "to minimum price."
                )
            })

        return attrs
