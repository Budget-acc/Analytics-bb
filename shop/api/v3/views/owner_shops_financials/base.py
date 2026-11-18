from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory

class OwnerShopsFinancialsViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Return monthly revenue/expense/profit for shops where request.user is OWNER.
    Revenue is calculated based on payment dates, not booking/sale creation dates.
    """
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        # UserFeaturePermission_AnyShop(feature_code="SHOPS_REVENUE_TRACKER", allowed_roles=["OWNER"]),
        FeatureRolePermissionFactory("SHOPS_REVENUE_TRACKER", allowed_roles=["OWNER"])
    ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.user = request.user
