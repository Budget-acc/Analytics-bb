import logging

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from .create import create
from .destroy import destroy
from .list import list as list_transfers
from .retrieve import retrieve

logger = logging.getLogger("api_requests")


class AccountTransferViewSet(StandardResponseMixin, ViewSet):
    permission_classes = [IsAuthenticated, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in AccountTransferViewSet: {e}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Your access to this shop has been deactivated.",
                "data": None,
            })

    create = create
    list = list_transfers
    retrieve = retrieve
    destroy = destroy
