from django.urls import path, include
from .views import AdminNotificationViewSet
from rest_framework.routers import DefaultRouter
 
router = DefaultRouter()
router.register(r'notifications', AdminNotificationViewSet, basename='admin-notifications')

urlpatterns = [
    path('', include(router.urls)),
]
