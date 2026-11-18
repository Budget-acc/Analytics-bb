from django.urls import path, include
from .views import TailorViewSet, TailorOrderViewSet, ProductWithBookingViewSet, TailorPaymentViewSet
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'tailor', TailorViewSet, basename='tailor')
router.register(r'tailor-order', TailorOrderViewSet, basename='tailororder')
router.register(r'convert-to-booking', ProductWithBookingViewSet, basename='convert-to-booking')
router.register(r'tailor-payment', TailorPaymentViewSet, basename='tailorpayment')

urlpatterns = [
    path("", include(router.urls)),
]
