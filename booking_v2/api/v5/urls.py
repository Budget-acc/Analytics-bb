from django.urls import path, include
from rest_framework.routers import DefaultRouter
from booking_v2.api.v5.views import (
    BookingViewSet,
    BookingDashboardView,
    LedgerSecurityAmountAPIView,
    PendingBookingAmountAPIView,
    PaymentDetailsGroupedAPIView,
)

router = DefaultRouter()
router.register(r"bookings", BookingViewSet, basename="booking")

urlpatterns = [
    path("", include(router.urls)),
    path("dashboard/", BookingDashboardView.as_view(), name="booking-dashboard-v5"),
    path("wallet/security-amounts/", LedgerSecurityAmountAPIView.as_view(), name="security-amount"),
    path("wallet/payment-details/", PaymentDetailsGroupedAPIView.as_view(), name="payment-details-grouped"),
    path("wallet/pending-amounts/", PendingBookingAmountAPIView.as_view(), name="pending-bookings"),
]
