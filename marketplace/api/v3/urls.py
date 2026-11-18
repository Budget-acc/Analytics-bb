from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    MarketplaceCategoryViewSet,
    MarketplaceSubCategoryViewSet,
    FabricViewSet,
    MarketplaceProductViewSet,
)
from .views.filter_apis import (
    ProductFilterOptionsAPIView,
)


router = DefaultRouter()
router.register(r'categories', MarketplaceCategoryViewSet, basename='marketplace-category')
router.register(r'subcategories', MarketplaceSubCategoryViewSet, basename='marketplace-subcategory')
router.register(r'fabrics', FabricViewSet, basename='fabric')
router.register(r'products', MarketplaceProductViewSet, basename='marketplace-product')

urlpatterns = [
    path('', include(router.urls)),
     path("products/filter-options",ProductFilterOptionsAPIView.as_view(),name="product-filter-options",),
]
