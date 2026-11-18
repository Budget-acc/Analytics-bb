import logging

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils import get_active_shop_id
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory

logger = logging.getLogger("api_requests")


class TrendingInsightsViewSetBase(StandardResponseMixin, APIView):
    """
    Shared base for the Trending Insights endpoints.

    Access is gated by the "TRENDING_INSIGHTS" subscription feature and
    restricted to OWNER/MANAGER (matching how shop/api/v4/views/staff_earnings
    gates "STAFF_ANALYTICS"). Shops without an active TRENDING_INSIGHTS feature
    subscription receive a permission error.
    """
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        FeatureRolePermissionFactory("TRENDING_INSIGHTS", allowed_roles=["OWNER", "MANAGER"]),
    ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in TrendingInsightsViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
