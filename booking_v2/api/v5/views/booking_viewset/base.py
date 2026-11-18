from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
import logging

logger = logging.getLogger("api_requests")


class BookingViewSet(StandardResponseMixin, viewsets.ViewSet):
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]
    authentication_classes = [CutoffJWTAuthentication]
    pagination_class = StandardPageNumberPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    VALID_PAYMENT_METHODS = ["gpay", "cash", "upi"]
    JSON_STRING_FIELDS = ['client', 'variants', 'additional_charges', 'details', 'remove_documents', 'payments']

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)         
        except Exception as e:
            logger.error(f"Shop permission denied in BookingViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
