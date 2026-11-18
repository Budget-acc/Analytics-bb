import logging

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import ManagementPermission

logger = logging.getLogger("api_requests")


class PurchaseViewSet(StandardResponseMixin, viewsets.ViewSet):
    """Purchase management - create/retrieve/list/update/destroy plus
    add-payment/delete-payment/send-invoice actions. Mirrors BookingViewSet's
    structure exactly (booking_v2/api/v5/views/booking_viewset/base.py)."""

    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ManagementPermission
    ]
    authentication_classes = [CutoffJWTAuthentication]
    pagination_class = StandardPageNumberPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    JSON_STRING_FIELDS = [
        "vendor", "existing_products", "new_products", "additional_charges", "paid_amount",
        # update() fields - only relevant for multipart requests to that
        # endpoint, harmless no-ops for create()'s multipart requests.
        "new_existing_products", "new_new_products", "update_existing_items", "delete_item_ids",
        "new_additional_charges", "update_additional_charges", "delete_additional_charge_ids",
        "delete_documents",
    ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in PurchaseViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None,
            })
