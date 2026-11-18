from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework_simplejwt.authentication import JWTAuthentication
from ERP.utils.mixins import StandardResponseMixin


class ShopWhatsAppManagementViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Admin management of a shop's WhatsApp setup — both the connection
    configuration (ShopWhatsAppConfiguration) and its message templates
    (ShopWhatsAppTemplate).

    Not router-registered (lookups differ by action: shop-scoped,
    configuration-id-scoped, or template-id-scoped) — actions are bound
    explicitly in urls.py via `.as_view({...})`. See:
      - retrieve_by_shop  (GET    .../shop/<shop_id>/)              — configuration + nested templates
      - create            (POST   .../shop/<shop_id>/)              — configuration only
      - partial_update    (PATCH  .../<pk>/)                        — configuration only, pk = configuration id
      - destroy           (DELETE .../<pk>/)                        — configuration only, pk = configuration id
      - create_template   (POST   .../templates/<pk>/)              — pk = configuration id
      - update_template   (PATCH  .../templates/<pk>/)              — pk = template id
      - destroy_template  (DELETE .../templates/<pk>/)               — pk = template id

    Response bodies never include a `status` field — the HTTP status code
    (200/201/400/404/...) is the sole indicator of outcome. The shared
    StandardResponseMixin/StandardResponse always adds `status` to the body,
    so the four helpers below strip it after building the response, rather
    than modifying that shared utility (used by other apps that still want it).
    """
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]

    def success_response(self, data=None, message=None, **kwargs):
        response = super().success_response(data=data, message=message, **kwargs)
        response.data.pop("status", None)
        return response

    def error_response(self, message=None, **kwargs):
        response = super().error_response(message=message, **kwargs)
        response.data.pop("status", None)
        return response

    def validation_error_response(self, errors=None, **kwargs):
        response = super().validation_error_response(errors=errors, **kwargs)
        response.data.pop("status", None)
        return response

    def not_found_response(self, resource=None, message=None, **kwargs):
        response = super().not_found_response(resource=resource, message=message, **kwargs)
        response.data.pop("status", None)
        return response
