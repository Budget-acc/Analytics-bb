from rest_framework import serializers

from expenses_v2.models import ShopCategory


VALID_CATEGORY_TYPES = ["income", "expense", "income_and_expense"]


class ShopCategoryReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopCategory
        fields = ["id", "name", "type", "is_active", "created_at"]


class ShopCategoryWriteSerializer(serializers.ModelSerializer):
    type = serializers.ChoiceField(choices=VALID_CATEGORY_TYPES)

    class Meta:
        model = ShopCategory
        fields = ["shop", "name", "type", "is_active"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Category name cannot be blank.")
        return value

    def validate_type(self, value):
        if value not in VALID_CATEGORY_TYPES:
            raise serializers.ValidationError(
                "Invalid category type. Must be one of: income, expense, income_and_expense."
            )
        return value

    def validate(self, attrs):
        shop = attrs.get("shop") or (self.instance.shop if self.instance else None)
        name = attrs.get("name", self.instance.name if self.instance else None)

        if shop and name:
            qs = ShopCategory.objects.filter(shop=shop, name__iexact=name)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"name": "A category with this name already exists for this shop."}
                )

        return attrs
