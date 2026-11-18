from django.urls import path
from booking_v2.api.v5_1.views import (
    PaymentDetailsGroupedAPIViewV51,
    BookingDashboardView
)

urlpatterns = [
    path("wallet/payment-details/", PaymentDetailsGroupedAPIViewV51.as_view(), name="v51-payment-details-grouped"),
    path("dashboard/", BookingDashboardView.as_view(), name="booking-dashboard-v5_1"),    
]
