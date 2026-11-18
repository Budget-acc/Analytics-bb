from django.urls import path, include
from rest_framework.routers import DefaultRouter
from shop.api.v4.views.staff_earnings import StaffEarningsViewSet

router = DefaultRouter()
router.register(r'staff-earnings', StaffEarningsViewSet, basename='staff-earnings-v4')

urlpatterns = [
    path('', include(router.urls)),
]
