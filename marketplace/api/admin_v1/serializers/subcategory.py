from rest_framework import serializers

from marketplace.models.marketplace_category import MarketplaceCategory
from marketplace.models.marketplace_subcategory import MarketplaceSubCategory


from rest_framework import serializers

from marketplace.models.marketplace_category import MarketplaceCategory
from marketplace.models.marketplace_subcategory import MarketplaceSubCategory


class MarketplaceSubCategorySerializer(serializers.ModelSerializer):

    category_name = serializers.CharField(
        source="category.name",
        read_only=True,
    )

    class Meta:
        model = MarketplaceSubCategory

        fields = [
            "id",
            "category",
            "category_name",
            "name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "category_name",
            "created_at",
            "updated_at",
        ]

    def validate_name(self, value):

        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Subcategory name cannot be empty."
            )

        queryset = MarketplaceSubCategory.all_objects.filter(
            name__iexact=value
        )

        if self.instance:
            queryset = queryset.exclude(
                pk=self.instance.pk
            )

        if queryset.exists():
            raise serializers.ValidationError(
                "A subcategory with this name already exists."
            )

        return value

    def validate_category(self, value):

        if not value.is_active:
            raise serializers.ValidationError(
                "Cannot assign a subcategory to an inactive category."
            )

        return value