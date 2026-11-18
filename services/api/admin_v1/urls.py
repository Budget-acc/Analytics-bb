from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import GeneralServiceViewSet, MainServiceCategoryViewSet, ProductAdminViewSet, ProductBulkImportViewSet

router = DefaultRouter()
router.register(r'admin/general-services', GeneralServiceViewSet, basename='generalservice')
router.register(r'admin/main-services', MainServiceCategoryViewSet, basename='main-service')
router.register(r'admin/product', ProductAdminViewSet, basename='admin-product')
router.register(r'admin/product-bulk-import', ProductBulkImportViewSet, basename='admin-product-bulk-import')

urlpatterns = [path('', include(router.urls))]
