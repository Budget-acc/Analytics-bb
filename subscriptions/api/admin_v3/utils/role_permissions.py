from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import BasePermission
from shop.api.v3.utils.shop import get_active_shop_and_user
from rest_framework.exceptions import APIException
from rest_framework import status
from subscriptions.models import ShopFeatureSubscription, has_feature_access
from .permissions import FeatureAccessException, _normalize_upper_set, _normalize_lower_set

class RoleBasedException(APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, detail=None, code=None, role=None, required_roles=None):
        self.detail = {
            'status': 'permission_denied',
            'message': detail or "You don't have the required role for this action.",
            'data': None,
            'dev_message': f"Required roles: {', '.join(required_roles or [])}. User role: {role}"
        }

class ShopRolePermission(BasePermission):
    """Permission class that restricts access based on user's role in a shop"""

    allowed_roles = ["OWNER", "MANAGER", "STAFF"]
    message = "You don't have the required role for this action."

    def has_permission(self, request, view):
        shop, shop_user = get_active_shop_and_user(request)

        if not shop_user:
            raise RoleBasedException(
                detail="You are not associated with this shop.",
                code="no_shop_association"
            )

        if shop_user.role not in self.allowed_roles:
            raise RoleBasedException(
                # detail=f"This action requires one of these roles: {', '.join(self.allowed_roles)}",
                detail="You don't have the permission.",
                code="insufficient_role",
                role=shop_user.role,
                required_roles=self.allowed_roles
            )

        return True

class OwnerOnlyPermission(ShopRolePermission):
    """Only shop owners can access"""
    allowed_roles = ["OWNER"]
    message = "Only shop owners can perform this action."

class ManagementPermission(ShopRolePermission):
    """Only shop owners and managers can access"""
    allowed_roles = ["OWNER", "MANAGER"]
    message = "Only shop management can perform this action."

class StaffExcludedPermission(ShopRolePermission):
    """Regular staff cannot access (only owners and managers)"""
    allowed_roles = ["OWNER", "MANAGER"]
    message = "Staff members cannot perform this action."

def FeatureRolePermissionFactory(feature_code, allowed_roles=None):
    """
    Factory that creates a new permission class checking both feature access
    and user role. Can be used directly in `permission_classes`.
    """
    allowed_roles = allowed_roles or ["OWNER"]

    class FeatureRolePermission(BasePermission):
        def _has_valid_feature_subscription(self, shop):
            subscription = getattr(shop, "subscription", None)
            if not subscription:
                return False

            now = timezone.localtime(timezone.now())
            return ShopFeatureSubscription.objects.filter(
                shop_subscription=subscription,
                feature__code=feature_code,
                is_active=True,
            ).filter(
                Q(end_date__isnull=True) | Q(end_date__gt=now)
            ).exists()

        def _get_handler(self, request, view):
            action = getattr(view, "action", None)
            if action and hasattr(view, action):
                return getattr(view, action)

            method_name = getattr(request, "method", "").lower()
            if method_name and hasattr(view, method_name):
                return getattr(view, method_name)

            return None

        def _is_exempt(self, request, view):
            if not view:
                return False

            if not getattr(view, "feature_validation_enabled", True):
                return True

            handler = self._get_handler(request, view)
            if handler and getattr(handler, "__feature_required__", False):
                return False

            action = str(getattr(view, "action", "") or "").lower()
            request_method = getattr(request, "method", "").upper()

            required_actions = _normalize_lower_set(getattr(view, "feature_required_actions", ()))
            required_methods = _normalize_upper_set(getattr(view, "feature_required_methods", ()))
            exempt_actions = _normalize_lower_set(getattr(view, "feature_exempt_actions", ()))
            exempt_methods = _normalize_upper_set(getattr(view, "feature_exempt_methods", ()))

            # Required policy always wins over exempt policy.
            if action and action in required_actions:
                return False
            if request_method in required_methods:
                return False

            if handler and getattr(handler, "__feature_exempt__", False):
                return True
            if action and action in exempt_actions:
                return True
            if request_method in exempt_methods:
                return True

            return False

        def has_permission(self, request, view):
            if self._is_exempt(request, view):
                return True

            shop, shop_user = get_active_shop_and_user(request)

            if not shop_user:
                raise RoleBasedException(
                    detail="You are not associated with this shop.",
                    code="no_shop_association"
                )

            # Check role permissions first
            if shop_user.role not in allowed_roles:
                raise RoleBasedException(
                    detail="You don't have permission to access this feature.",
                    code="insufficient_role",
                    role=shop_user.role,
                    required_roles=allowed_roles
                )

            # Then check feature access
            if not has_feature_access(shop_user, feature_code, require_active_subscription=False):
                raise FeatureAccessException(
                    detail="Your subscription does not include this feature.",
                    code="feature_not_subscribed",
                    feature_code=feature_code
                )

            if not self._has_valid_feature_subscription(shop):
                raise FeatureAccessException(
                    detail="Your feature subscription has expired.",
                    code="feature_subscription_expired",
                    feature_code=feature_code,
                )

            return True

    return FeatureRolePermission
