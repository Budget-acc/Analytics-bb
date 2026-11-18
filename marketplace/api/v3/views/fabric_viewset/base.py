from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from marketplace.models.fabric import Fabric
from marketplace.api.v3.serializers.fabric import FabricSerializer

from drf_spectacular.utils import extend_schema


class FabricViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Client-facing fabric ViewSet.
    Provides read-only access to active fabrics.
    """
    
    permission_classes = [AllowAny]
    serializer_class = FabricSerializer
    queryset = Fabric.objects.filter(is_active=True).select_related('category').order_by('name')
    
    @extend_schema(tags=["Marketplace V3"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @extend_schema(tags=["Marketplace V3"])
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
