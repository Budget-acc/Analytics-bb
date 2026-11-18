from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from subscriptions.api.admin_v3.utils.role_permissions import ManagementPermission
from shop.api.v3.utils.shop import get_active_shop_id
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class TailorViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    ViewSet to manage Tailors (CRUD) per shop.
    """
    permission_classes = [IsAuthenticated, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]

    def get_permissions(self):
        """
        Customize permissions:
        - list & retrieve: only IsAuthenticated
        - others (create, update, destroy): IsAuthenticated + OwnerOnlyPermission
        """
        if self.action in ["list", "retrieve"]:
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [IsAuthenticated, ManagementPermission, SubscriptionActivePermission]
        return [permission() for permission in permission_classes]


    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in TailorViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
