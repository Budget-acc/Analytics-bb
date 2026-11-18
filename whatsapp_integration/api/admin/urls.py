from django.urls import path

from .views import ShopWhatsAppManagementViewSet

urlpatterns = [
    path(
        "admin/configurations/shop/<int:shop_id>/",
        ShopWhatsAppManagementViewSet.as_view({"get": "retrieve_by_shop", "post": "create"}),
        name="whatsapp-configuration-by-shop",
    ),
    path(
        "admin/configurations/<int:pk>/",
        ShopWhatsAppManagementViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="whatsapp-configuration-detail",
    ),
    path(
        "admin/configurations/templates/<int:pk>/",
        ShopWhatsAppManagementViewSet.as_view({
            "post": "create_template",
            "patch": "update_template",
            "delete": "destroy_template",
        }),
        name="whatsapp-configuration-templates",
    ),
]
