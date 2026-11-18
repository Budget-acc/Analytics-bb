from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory


class ProductTransferViewSet(StandardResponseMixin, viewsets.ViewSet):
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        FeatureRolePermissionFactory("TRANSFER", allowed_roles=["OWNER", "MANAGER"]),
    ]
    authentication_classes = [CutoffJWTAuthentication]

    ALLOWED_ROLES = ["OWNER", "MANAGER"]
