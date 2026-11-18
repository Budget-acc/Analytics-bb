from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from subscriptions.models import Feature, ShopUserFeature
from shop.models import ShopUser
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin


class ShopUserFeatureViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Grants/blocks a Feature at user-level for a specific shop user.
    """

    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAdminUserOnly]

    def _resolve_shop_user(self, user_id: int, shop_id: int = None):
        try:
            qs = ShopUser.objects.filter(user_id=user_id, is_active=True)

            if shop_id:
                qs = qs.filter(shop_id=shop_id)

            count = qs.count()

            if count == 0:
                return None, "Shop user not found."

            if count > 1 and not shop_id:
                return qs.order_by("id").first(), None

            return qs.first(), None

        except Exception:
            return None, "Internal lookup failed."


    def _get_feature(self, feature_id: int):
        try:
            feature = Feature.objects.only("id", "code", "name").get(id=feature_id, is_active=True)
            return feature, None
        except Feature.DoesNotExist:
            return None, "Feature does not exist or inactive."
        except Exception:
            return None, "Internal feature lookup failed."
