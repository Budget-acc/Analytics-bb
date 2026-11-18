from rest_framework import serializers

from shop.models import ShopTaxConfiguration


class ShopTaxConfigurationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopTaxConfiguration
        fields = [
            'shop',
            'tax_name',
            'tax_rate',
            'is_enabled',
            'taxable_components',
            'tax_calculation_type',
        ]

    def validate_tax_rate(self, value):
        if value < 0:
            raise serializers.ValidationError("Must be zero or a positive value.")
        return value

    def validate_taxable_components(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Must be a list of taxable component values.")
        valid_values = {choice.value for choice in ShopTaxConfiguration.TaxableComponent}
        invalid = [v for v in value if v not in valid_values]
        if invalid:
            raise serializers.ValidationError(
                f"Invalid taxable component(s): {', '.join(str(v) for v in invalid)}."
            )
        return value

    def validate(self, attrs):
        # Merge against the existing instance (for partial updates) so the
        # cross-field rule is checked against the resulting state, not just
        # whichever fields happen to be present in this particular request.
        is_enabled = attrs.get(
            'is_enabled', getattr(self.instance, 'is_enabled', False)
        )
        taxable_components = attrs.get(
            'taxable_components', getattr(self.instance, 'taxable_components', None)
        )
        if is_enabled and not taxable_components:
            raise serializers.ValidationError(
                "taxable_components must be non-empty when is_enabled is true."
            )
        return attrs


class ShopTaxConfigurationReadSerializer(serializers.ModelSerializer):
    shop_id = serializers.IntegerField(source='shop.id', read_only=True)
    shop_name = serializers.CharField(source='shop.name', read_only=True)

    class Meta:
        model = ShopTaxConfiguration
        fields = [
            'id',
            'shop_id',
            'shop_name',
            'tax_name',
            'tax_rate',
            'is_enabled',
            'taxable_components',
            'tax_calculation_type',
            'created_at',
            'updated_at',
        ]
