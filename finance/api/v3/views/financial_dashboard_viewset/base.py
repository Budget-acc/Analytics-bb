from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id
import logging
from rest_framework.exceptions import PermissionDenied

logger = logging.getLogger("api_requests")

class FinancialDashboardViewSetBase(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)         
        except Exception as e:
            logger.error(f"Shop permission denied in FinancialDashboardViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
