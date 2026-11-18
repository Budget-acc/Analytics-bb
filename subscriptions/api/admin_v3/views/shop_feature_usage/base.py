from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin


class ShopFeatureUsageViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Admin viewset for ShopFeatureUsage.

    Provides list/retrieve for browsing usage records, and a dedicated
    reset action that writes an audit log entry on every change.
    """

    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAdminUserOnly]
