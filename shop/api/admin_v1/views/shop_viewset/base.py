from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from ERP.utils.mixins import StandardResponseMixin


class ShopViewSet(viewsets.ViewSet, StandardResponseMixin):
    """
    Handles CRUD for Shop with manual validation and soft-delete awareness.
    """
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def safe_subscription_status(self, latest, fallback):
        if latest:
            return latest
        if fallback:
            return fallback
        raise ValueError("Missing subscription status for shop. Data integrity issue.")
