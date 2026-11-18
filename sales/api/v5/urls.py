from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import SaleViewSet, GlobalSearchViewSet

router = DefaultRouter()
router.register(r"sales", SaleViewSet, basename="sales-v5")
router.register(r'global-search', GlobalSearchViewSet, basename='global-search-v5')

urlpatterns = [
    path("", include(router.urls)),
]
