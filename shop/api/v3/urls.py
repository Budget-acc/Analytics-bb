from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (ShopSettingsViewSet, StaffViewSet, StaffEarningsViewSet,
    ShopActivityLogViewSet, BugReportViewSet, OwnerShopsFinancialsViewSet,
    UserAssociatedShopsView, ShopPrivacySettingsView, ShopTaxConfigurationViewSet)

router = DefaultRouter()
router.register(r'shop-settings', ShopSettingsViewSet, basename='shop-settings')
router.register(r'staff', StaffViewSet, basename='staff')
router.register(r'staff-earnings', StaffEarningsViewSet, basename='staff-earnings')
router.register(r'shop-activity-logs', ShopActivityLogViewSet, basename='shop-activity-logs')
router.register(r'bug-report', BugReportViewSet, basename='bug-report')
router.register(r'shops-financials', OwnerShopsFinancialsViewSet, basename='shops-financials')
router.register(r'shop-tax-configuration', ShopTaxConfigurationViewSet, basename='shop-tax-configuration')

urlpatterns = [
    path('', include(router.urls)),
    path('available-shops/', UserAssociatedShopsView.as_view(), name='available-shops'),
    path('shop-privacy-settings/', ShopPrivacySettingsView.as_view(), name='shop-privacy-settings'),
]
