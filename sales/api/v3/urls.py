from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import SaleViewSet, SaleDetailAmountAPIView

router = DefaultRouter()
router.register(r"sales", SaleViewSet, basename="sales")

urlpatterns = [
    path("", include(router.urls)),
    path("wallet/sales/", SaleDetailAmountAPIView.as_view(), name="sale-detail-amount"),
]
