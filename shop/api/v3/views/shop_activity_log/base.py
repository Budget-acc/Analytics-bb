from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils import get_active_shop_id
from ERP.utils.mixins import StandardResponseMixin
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger(__name__)


class ShopActivityLogViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Viewset to list ShopActivityLog for the active shop.
    Supports filters: q, action, user, content_type, start_date, end_date
    """
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    SAFE_DISPLAY_FIELDS = [
        "name", "shop_booking_id", "shop_sale_id", "client_name", "full_name", "title"
    ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in ShopActivityLogViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
