import logging

from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils.shop import get_active_shop_id, get_active_shop_and_user
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")

VALID_ACCOUNT_TYPES = {"cash", "bank"}


class AccountViewSetBase(StandardResponseMixin, viewsets.ViewSet):
    permission_classes = [IsAuthenticated, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]
    pagination_class = StandardPageNumberPagination

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Shop permission denied in AccountViewSet: {e}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None,
            })

    def _require_owner(self, request):
        """
        Returns (shop, shop_user) if the caller is a shop OWNER.
        Raises PermissionDenied with a structured response otherwise.
        Only call this from mutating actions (create / partial_update / destroy).
        """
        try:
            shop, shop_user = get_active_shop_and_user(request)
        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error resolving shop/user in AccountViewSet: {e}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Unable to verify shop access.",
                "data": None,
            })

        if shop_user.role != "OWNER":
            logger.warning(
                f"AccountViewSet: role='{shop_user.role}' attempted owner-only action "
                f"(shop={self.shop_id}, user={request.user.id})"
            )
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Only the shop owner can manage accounts.",
                "dev_message": (
                    f"ShopUser role='{shop_user.role}' attempted owner-only action "
                    f"for shop {self.shop_id}."
                ),
                "data": None,
            })

        return shop, shop_user
