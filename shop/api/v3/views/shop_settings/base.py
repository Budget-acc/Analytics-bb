from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from subscriptions.api.admin_v3.utils.role_permissions import OwnerOnlyPermission
from ERP.utils.mixins import StandardResponseMixin
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

class ShopSettingsViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    View to retrieve or update Shop Settings (e.g. cooling days).
    """
    permission_classes = [
        IsAuthenticated, 
        OwnerOnlyPermission,
        SubscriptionActivePermission
        ]
    authentication_classes = [CutoffJWTAuthentication]
