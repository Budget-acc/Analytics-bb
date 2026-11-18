import logging

from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.role_permissions import  OwnerOnlyPermission
logger = logging.getLogger("api_requests")


class DailySummaryViewSetBase(StandardResponseMixin, viewsets.ViewSet):
    permission_classes = [
        IsAuthenticated,
        OwnerOnlyPermission
        ]
    authentication_classes = [CutoffJWTAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Unexpected shop resolution error in DailySummaryViewSet: {e}", exc_info=True)
            raise
