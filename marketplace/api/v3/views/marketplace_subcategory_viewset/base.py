from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from marketplace.models.marketplace_subcategory import MarketplaceSubCategory
from marketplace.api.v3.serializers.subcategory import MarketplaceSubCategorySerializer

from drf_spectacular.utils import extend_schema


class MarketplaceSubCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Client-facing marketplace subcategory ViewSet.
    Provides read-only access to active subcategories.
    """
    
    permission_classes = [AllowAny]
    serializer_class = MarketplaceSubCategorySerializer
    
    def get_queryset(self):
        """Return only active subcategories with active parent categories"""
        return MarketplaceSubCategory.objects.filter(
            is_active=True,
            category__is_active=True
        ).select_related('category').order_by('category__name', 'name')
    
    @extend_schema(tags=["Marketplace V3"])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @extend_schema(tags=["Marketplace V3"])
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
