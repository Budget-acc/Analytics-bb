from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.vendor_viewset import VendorViewSet
from .views.purchase_viewset import PurchaseViewSet

router = DefaultRouter()
router.register(r'vendors', VendorViewSet, basename='vendor')
router.register(r'purchases', PurchaseViewSet, basename='purchase')

urlpatterns = [
    path('', include(router.urls)),
]

