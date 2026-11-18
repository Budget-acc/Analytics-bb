from rest_framework import serializers
from services.models import ProductVariant


class PublicProductVariantSerializer(serializers.ModelSerializer):
    """
    Serializer for a single product variant in the public catalog.
    remaining_stock is not a model field — it is injected by the view after
    the availability calculation.
    """

    class Meta:
        model = ProductVariant
        fields = ["id", "attribute", "stock"]
