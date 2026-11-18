import logging

from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import OwnerOnlyPermission

logger = logging.getLogger("api_requests")


class ShopTaxConfigurationViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    ViewSet to manage a shop's tax (e.g. GST) configuration — a single
    configuration row per shop covering the tax rate, whether it's enabled,
    and which invoice components it applies to.
    """
    permission_classes = [IsAuthenticated, OwnerOnlyPermission, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in ShopTaxConfigurationViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
