from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from marketplace.models.marketplace_category import MarketplaceCategory
from marketplace.api.v3.serializers.category import MarketplaceCategorySerializer

from drf_spectacular.utils import extend_schema


class MarketplaceCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Client-facing marketplace category ViewSet.
    Provides read-only access to active categories.
    """
    
    permission_classes = [AllowAny]
    serializer_class = MarketplaceCategorySerializer
    queryset = MarketplaceCategory.objects.filter(is_active=True).order_by('name')
    
    @extend_schema(tags=["Marketplace V3"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @extend_schema(tags=["Marketplace V3"])
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
