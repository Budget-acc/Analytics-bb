from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from authentication.permissions import IsAdminUserOnly
from shop.models import ShopUser

BASE_USER_LIMIT_IN_SHOP = 3


class ShopUserLinkAPIView(APIView):
    """
    API endpoints for linking users to shops with role-based limits.

    Role Limits:
    - OWNER: 1 per shop
    - MANAGER: 1 per shop
    - STAFF: 2 per shop
    """
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]

    def _check_shop_user_limit(self, shop, exclude_shop_user_id=None):
        effective_limit = BASE_USER_LIMIT_IN_SHOP + shop.extra_user_limit

        qs = ShopUser.objects.filter(
            shop=shop,
            is_active=True
        )

        if exclude_shop_user_id:
            qs = qs.exclude(id=exclude_shop_user_id)

        current_count = qs.count()

        if current_count >= effective_limit:
            return False, current_count, effective_limit

        return True, current_count, effective_limit
