# user_permission_class.py
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import APIException
from rest_framework import status
from subscriptions.models import ShopUserFeature, Feature
from shop.api.v3.utils.shop import get_active_shop_and_user
from shop.models import ShopUser

class FeatureDenied(APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, detail, feature, role):
        self.detail = {
            "status": "permission_denied",
            "message": detail,
            "feature": feature,
            "role": role,
        }


def UserFeaturePermission_SingleShop(feature_code, allowed_roles=None):
    allowed_roles = allowed_roles or []

    class UserFeaturePermission(BasePermission):

        def has_permission(self, request, view):
            # returns (shop, shop_user)
            shop, shop_user = get_active_shop_and_user(request)

            # 1 Must be associated with shop
            if not shop_user:
                raise FeatureDenied(
                    detail="User not assigned to any shop",
                    feature=feature_code,
                    role=None
                )

            # 2️ Role must be allowed
            if allowed_roles and shop_user.role not in allowed_roles:
                raise FeatureDenied(
                    detail=f"Role cannot access this feature",
                    feature=feature_code,
                    role=shop_user.role
                )

            # 3️ Feature must exist
            if not Feature.objects.filter(code=feature_code).exists():
                raise FeatureDenied(
                    detail=f"Feature does not exist",
                    feature=feature_code,
                    role=shop_user.role
                )

            # 4️ MUST HAVE ShopUserFeature record
            exists = ShopUserFeature.objects.filter(
                shop_user=shop_user,
                feature__code=feature_code,
                granted=True
            ).exists()

            if not exists:
                raise FeatureDenied(
                    detail="Feature is not assigned in this shop",
                    feature=feature_code,
                    role=shop_user.role
                )
            return True

    return UserFeaturePermission



def UserFeaturePermission_AnyShop(feature_code, allowed_roles=None):
    allowed_roles = allowed_roles or []

    class UserFeaturePermission(BasePermission):

        def has_permission(self, request, view):
            user = request.user

            # 1. User must be shop-related account
            shop_users = ShopUser.objects.filter(user=user, is_active=True)

            if not shop_users.exists():
                raise FeatureDenied(
                    detail="User is not linked to any shop",
                    feature=feature_code,
                    role=None
                )

            # 2. Role filtering
            if allowed_roles:
                role_match = shop_users.filter(role__in=allowed_roles).exists()
                if not role_match:
                    raise FeatureDenied(
                        detail=f"None of user's roles allowed. Required: {allowed_roles}",
                        feature=feature_code,
                        role=None
                    )

            # 3. Feature existence
            if not Feature.objects.filter(code=feature_code).exists():
                raise FeatureDenied(
                    detail=f"Feature '{feature_code}' does not exist",
                    feature=feature_code,
                    role=None
                )

            # 4. Check feature granted in ANY shop-user membership
            has_access = ShopUserFeature.objects.filter(
                shop_user__in=shop_users,
                feature__code=feature_code,
                granted=True,
            ).exists()

            if not has_access:
                raise FeatureDenied(
                    detail="Feature not assigned in any of user's shops",
                    feature=feature_code,
                    role=None
                )

            return True

    return UserFeaturePermission
