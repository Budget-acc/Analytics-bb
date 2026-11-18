from django.urls import path, include
from rest_framework.routers import DefaultRouter
from booking_v2.api.v3.views import (
    BookingViewSet,
    ClientViewSet,
    BookingDashboardView,
    PaymentDetailsGroupedAPIView,
    PendingBookingAmountAPIView,
    PendingSecurityAmountAPIView,
    BookingDetailAmountAPIView,
)

router = DefaultRouter()
router.register(r"bookings", BookingViewSet, basename="bookings")
router.register(r"clients", ClientViewSet, basename="clients")

urlpatterns = [
    path("", include(router.urls)),
    path("dashboard/", BookingDashboardView.as_view(), name="booking-dashboard"),
    path("wallet/payment-details/", PaymentDetailsGroupedAPIView.as_view(), name="payment-details-grouped"),
    path("wallet/pending-amounts/", PendingBookingAmountAPIView.as_view(), name="pending-bookings"),
    path("wallet/security-amounts/", PendingSecurityAmountAPIView.as_view(), name="security-amount"),
    path("wallet/booking-amounts/", BookingDetailAmountAPIView.as_view(), name="booking-detail-amount"),
]
