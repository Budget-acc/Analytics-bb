from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory
from shop.api.v3.utils.shop import get_active_shop_id
import logging

logger = logging.getLogger("api_requests")


class TailorOrderViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    ViewSet to handle Tailor Orders (create, list, retrieve, update)
    """
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        FeatureRolePermissionFactory("CUSTOMIZATION_WORK", allowed_roles=["OWNER", "MANAGER", "STAFF"]),

        ]
    authentication_classes = [CutoffJWTAuthentication]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    # --------------------------- INITIAL ---------------------------
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in TailorOrderViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
