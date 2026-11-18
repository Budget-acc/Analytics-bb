from django.urls import path
from .views.fabric_apis import FabricListCreateAPIView, FabricDetailAPIView
from .views.category_apis import (
    MarketplaceCategoryListCreateAPIView,
    MarketplaceCategoryDetailAPIView,
)
from .views.sub_category import (
    MarketplaceSubCategoryListCreateAPIView,
    MarketplaceSubCategoryDetailAPIView,
)

from .views.product_apis import MarketplaceProductListCreateAPIView,MarketplaceProductDetailAPIView
from .views.product_media import MarketplaceProductMediaPresignedURLAPIView,MarketplaceProductMediaAPIView


urlpatterns = [
    # Fabric Endpoints
    path("fabrics/", FabricListCreateAPIView.as_view(), name="marketplace-fabric-list-create"),
    path("fabrics/<int:pk>/", FabricDetailAPIView.as_view(), name="marketplace-fabric-detail"),
    
    # Category Endpoints
    path("categories/", MarketplaceCategoryListCreateAPIView.as_view(), name="marketplace-category-list-create"),
    path("categories/<int:pk>/", MarketplaceCategoryDetailAPIView.as_view(), name="marketplace-category-detail"),
 
 
    # SubCategory
    path("subcategories/",MarketplaceSubCategoryListCreateAPIView.as_view(),name="subcategory-list-create",),
    path("subcategories/<int:pk>/",MarketplaceSubCategoryDetailAPIView.as_view(),name="subcategory-detail",),


    # -----------------------------------------
    # Product
    # -----------------------------------------

    path("products/",MarketplaceProductListCreateAPIView.as_view(),name="marketplace-product-list-create",),
    path("products/<int:pk>/",MarketplaceProductDetailAPIView.as_view(),name="marketplace-product-detail",),

    # -----------------------------------------
    # Product Media
    # -----------------------------------------

    path("products/<int:product_id>/media/presigned-urls/",MarketplaceProductMediaPresignedURLAPIView.as_view(),name="marketplace-product-media-presigned-urls",),
    path("products/<int:product_id>/media/",MarketplaceProductMediaAPIView.as_view(),name="marketplace-product-media",),
    
]