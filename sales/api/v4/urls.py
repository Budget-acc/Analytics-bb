from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import SaleViewSet, SaleDetailAmountAPIView, GlobalSearchViewSet

router = DefaultRouter()
router.register(r"sales", SaleViewSet, basename="sales")
router.register(r'global-search', GlobalSearchViewSet, basename='global-search')

urlpatterns = [
    path("", include(router.urls)),
    path("wallet/sales/", SaleDetailAmountAPIView.as_view(), name="sale-detail-amount"),
]
