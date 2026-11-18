from rest_framework import serializers
from marketplace.models.marketplace_subcategory import MarketplaceSubCategory


class MarketplaceSubCategorySerializer(serializers.ModelSerializer):
    """
    Client-facing subcategory serializer.
    Returns only public information about subcategories.
    """
    
    category_id = serializers.IntegerField(source='category.id', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = MarketplaceSubCategory
        fields = [
            'id',
            'name',
            'description',
            'category_id',
            'category_name',
            'created_at',
        ]
        read_only_fields = fields
