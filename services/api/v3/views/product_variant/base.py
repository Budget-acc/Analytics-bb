from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission


class ProductVariantViewSet(StandardResponseMixin, viewsets.ViewSet):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)

        try:
            self.shop_id = get_active_shop_id(request)
        except Exception:
            self.permission_denied(
                request,
                message="You do not have permission to access this shop."
            )
