from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import AdminLoginView, AdminViewSet, UserViewSet, ModelSchemaView

router = DefaultRouter()
router.register(r"auth/admin/users", UserViewSet, basename="user")
router.register(r"auth/admin", AdminViewSet, basename="admin")

urlpatterns = [
    path("auth/admin/login/", AdminLoginView.as_view(), name="admin-login"),
    # Internal/admin-only: live Django model schema (fields + relationships).
    # IsAdminUserOnly-gated - see authentication/api/admin_v1/views/model_schema_view.py.
    path("auth/admin/model-schema/", ModelSchemaView.as_view(), name="model-schema"),
]

urlpatterns += router.urls
