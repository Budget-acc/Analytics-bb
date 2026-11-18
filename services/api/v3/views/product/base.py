from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from services.api.v3.views.product.helpers.exceptions import ProductSearchThrottle
from shop.api.v3.utils.shop import get_active_shop_id
from django.core.exceptions import PermissionDenied
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class ProductViewSet(StandardResponseMixin, viewsets.ViewSet):
    """..."""
    authentication_classes = [CutoffJWTAuthentication]
    throttle_classes = [ProductSearchThrottle]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]    

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
