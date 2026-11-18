from django.urls import path, include
from rest_framework.routers import DefaultRouter
from subscriptions.api.v3.views import SubscriptionDetailsViewSet

router = DefaultRouter()
router.register(r"subscription-details", SubscriptionDetailsViewSet, basename="subscription-details")

urlpatterns = [
    path("", include(router.urls)),
]
