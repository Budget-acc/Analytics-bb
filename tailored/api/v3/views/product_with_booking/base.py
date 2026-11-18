from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser, FormParser
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class ProductWithBookingViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Combined endpoint to create multiple products, variants, and bookings in a single request.
    """
    permission_classes = [IsAuthenticated, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
