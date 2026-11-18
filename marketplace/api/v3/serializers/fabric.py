from rest_framework import serializers
from marketplace.models.fabric import Fabric


class FabricSerializer(serializers.ModelSerializer):
    """
    Client-facing fabric serializer.
    Returns only public information about fabrics.
    """
    
    category = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Fabric
        fields = [
            'id',
            'name',
            'created_at',
        ]
        read_only_fields = fields
