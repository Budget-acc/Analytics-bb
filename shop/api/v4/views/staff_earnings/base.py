from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory
from shop.api.v3.utils import get_active_shop_id
from ERP.utils.mixins import StandardResponseMixin
import logging

logs = logging.getLogger("api_requests")


class StaffEarningsViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    ViewSet to get staff earnings reports with monthly breakdowns (V4 — includes Tailor Orders)
    """
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        FeatureRolePermissionFactory("STAFF_ANALYTICS", allowed_roles=["OWNER", "MANAGER"]),
        ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logs.error(f"Shop permission denied in StaffEarningsViewSet (V4): {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
