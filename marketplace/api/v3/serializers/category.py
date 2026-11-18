from rest_framework import serializers
from marketplace.models.marketplace_category import MarketplaceCategory


class MarketplaceCategorySerializer(serializers.ModelSerializer):
    """
    Client-facing category serializer.
    Returns only public information about categories.
    """
    
    class Meta:
        model = MarketplaceCategory
        fields = [
            'id',
            'name',
            'description',
            'created_at',
        ]
        read_only_fields = fields
