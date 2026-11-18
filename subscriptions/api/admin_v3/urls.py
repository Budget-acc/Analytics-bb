from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SubscriptionPlanViewSet, FeatureViewSet, ShopSubscriptionViewSet, ShopSubscriptionDetailsViewSet, ShopUserFeatureViewSet, ShopFeatureUsageViewSet

router = DefaultRouter()
router.register(r'admin/subscription-plans', SubscriptionPlanViewSet, basename='subscription-plans')
router.register(r'admin/features', FeatureViewSet, basename='features')
router.register(r'admin/shop-subscriptions', ShopSubscriptionViewSet)
router.register(r'admin/shop-subscription-details', ShopSubscriptionDetailsViewSet, basename='shop-subscription-details')
router.register(r'admin/shop-user-feature', ShopUserFeatureViewSet, basename='shop-user=features')
router.register(r'admin/shop-feature-usage', ShopFeatureUsageViewSet, basename='shop-feature-usage')


urlpatterns = [
    path('', include(router.urls)),
]
