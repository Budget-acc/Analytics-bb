from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, ProductVariantViewSet, ProductTransferViewSet, AllServicesView, SelectedServicesView, ReorderServicesView, ShopProductSearchAPIView
from .views import ProductStockAdjustmentViewSet

router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='product')
router.register(r'products/(?P<product_id>\d+)/variants', ProductVariantViewSet, basename='product-variant')
router.register(r'transfer-product', ProductTransferViewSet, basename='transfer-product')
router.register(r'products/(?P<product_id>\d+)/stock-adjustments', ProductStockAdjustmentViewSet, basename='product-stock-adjustment')

urlpatterns = [
    path('', include(router.urls)),
    path('all/', AllServicesView.as_view(), name='all-services'),
    path('selected/', SelectedServicesView.as_view(), name='selected-services'),
    path('service-reorder/', ReorderServicesView.as_view(), name='reorder-services'),
    path('product-search/', ShopProductSearchAPIView.as_view(), name='product-search'),
]
