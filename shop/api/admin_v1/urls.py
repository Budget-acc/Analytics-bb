from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ShopViewSet, ShopUserViewSet, BugReportAdminViewSet, ShopUserLinkAPIView, UserAssociatedShopsListView, OrganizationViewSet

router = DefaultRouter()
router.register(r'admin/shops', ShopViewSet, basename='shop')
router.register(r'admin/shop-with-user', ShopUserViewSet, basename='shop-with-user')
router.register(r'admin/bugreports', BugReportAdminViewSet, basename='bugreport')
router.register(r'admin/organizations', OrganizationViewSet, basename='organization')

urlpatterns = [
    path('', include(router.urls)),
    path('admin/link-user/', ShopUserLinkAPIView.as_view()),
    path('admin/link-user/<int:shop_user_id>/', ShopUserLinkAPIView.as_view()),
    path('admin/user-shops/', UserAssociatedShopsListView.as_view()),
]
