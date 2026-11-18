from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from shop.api.v3.utils.shop import get_active_shop_and_user
from subscriptions.models import has_feature_access


def _normalize_upper_set(values):
    if not values:
        return set()
    return {str(v).upper() for v in values}


def _normalize_lower_set(values):
    if not values:
        return set()
    return {str(v).lower() for v in values}


def skip_subscription_validation(func):
    """
    Decorator to bypass only active-subscription validation for a specific handler method/function.
    Works for APIView methods, ViewSet action methods, and utility callable handlers.
    """
    setattr(func, "__subscription_exempt__", True)
    return func


def require_subscription_validation(func):
    """
    Decorator to force subscription validation for a specific handler method/function.
    Takes precedence over skip flags.
    """
    setattr(func, "__subscription_required__", True)
    return func


def skip_feature_validation(func):
    """
    Decorator to bypass feature-level validation for a specific handler method/function.
    This is independent from subscription validation.
    """
    setattr(func, "__feature_exempt__", True)
    return func


def require_feature_validation(func):
    """
    Decorator to force feature-level validation for a specific handler method/function.
    Takes precedence over skip flags.
    """
    setattr(func, "__feature_required__", True)
    return func


class SubscriptionPolicyMixin:
    """
    Optional mixin for views to declare subscription policy.
    Defaults to global enforcement with safe OPTIONS/HEAD bypass.
    """

    subscription_validation_enabled = True
    subscription_exempt_actions = set()
    subscription_exempt_methods = {"OPTIONS", "HEAD"}
    subscription_required_actions = set()
    subscription_required_methods = set()


class FeaturePolicyMixin:
    """
    Optional mixin for views to declare feature-validation policy.
    Defaults to global enforcement with safe OPTIONS/HEAD bypass.
    """

    feature_validation_enabled = True
    feature_exempt_actions = set()
    feature_exempt_methods = {"OPTIONS", "HEAD"}
    feature_required_actions = set()
    feature_required_methods = set()


class SubscriptionRequiredException(APIException):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = "Shop subscription required"
    default_code = "subscription_required"

    def __init__(self, detail=None, code=None):
        resolved_code = code or self.default_code
        self.detail = {
            "status": resolved_code,
            "message": detail or self.default_detail,
            "dev_message": resolved_code,
        }


class SubscriptionActivePermission(BasePermission):
    message = "Shop does not have an active subscription."

    def _get_handler(self, request, view):
        action = getattr(view, "action", None)
        if action and hasattr(view, action):
            return getattr(view, action)

        method_name = getattr(request, "method", "").lower()
        if method_name and hasattr(view, method_name):
            return getattr(view, method_name)

        return None

    def _resolve_policy(self, request, view):
        return {
            "enabled": getattr(view, "subscription_validation_enabled", True),
            "exempt_actions": _normalize_lower_set(getattr(view, "subscription_exempt_actions", ())),
            "exempt_methods": _normalize_upper_set(getattr(view, "subscription_exempt_methods", ())),
            "required_actions": _normalize_lower_set(getattr(view, "subscription_required_actions", ())),
            "required_methods": _normalize_upper_set(getattr(view, "subscription_required_methods", ())),
        }

    def _is_exempt(self, request, view):
        policy = self._resolve_policy(request, view)
        if not policy["enabled"]:
            return True

        handler = self._get_handler(request, view)
        if handler and getattr(handler, "__subscription_required__", False):
            return False

        action = str(getattr(view, "action", "") or "").lower()
        request_method = getattr(request, "method", "").upper()

        # Required policy always wins over exempt policy.
        if action and action in policy["required_actions"]:
            return False
        if request_method in policy["required_methods"]:
            return False

        if handler and getattr(handler, "__subscription_exempt__", False):
            return True
        if action and action in policy["exempt_actions"]:
            return True
        if request_method in policy["exempt_methods"]:
            return True

        return False

    def has_permission(self, request, view):
        if view and self._is_exempt(request, view):
            return True

        shop, shop_user = get_active_shop_and_user(request)

        try:
            subscription = shop.subscription

            if not subscription:
                raise SubscriptionRequiredException(
                    detail="Shop has no subscription.",
                    code="subscription_missing",
                )

        except ObjectDoesNotExist:
            raise SubscriptionRequiredException(
                detail="Shop has no subscription.",
                code="subscription_missing",
            )

        now = timezone.localtime(timezone.now())
        subscription_end_date = timezone.localtime(subscription.end_date) if subscription.end_date else None
        if subscription_end_date and subscription_end_date <= now:
            raise SubscriptionRequiredException(
                detail="Shop subscription has expired.",
                code="subscription_expired",
            )

        if subscription.status != "ACTIVE":
            raise SubscriptionRequiredException(
                detail=f"Shop subscription is not active (status: {subscription.status}).",
                code="subscription_inactive",
            )

        return True


class FeatureAccessException(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Feature access denied"
    default_code = "feature_access_denied"

    def __init__(self, detail=None, code=None, feature_code=None):
        resolved_code = code or self.default_code
        self.detail = {
            "status": resolved_code,
            "message": detail or self.default_detail,
            "dev_message": resolved_code,
            "feature": feature_code,
        }


def FeaturePermissionFactory(feature_code):
    class FeaturePermission(BasePermission):
        message = f"Feature {feature_code} not available."

        def has_permission(self, request, view):
            try:
                shop, shop_user = get_active_shop_and_user(request)

                if not shop_user:
                    raise FeatureAccessException(
                        detail="User not associated with this shop.",
                        code="shop_user_missing",
                        feature_code=feature_code,
                    )

                if not has_feature_access(shop_user, feature_code, require_active_subscription=False):
                    raise FeatureAccessException(
                        detail=f"You are not authorized to use {feature_code}.",
                        code="feature_denied",
                        feature_code=feature_code,
                    )

                return True

            except ObjectDoesNotExist:
                raise SubscriptionRequiredException(
                    detail="Subscription or shop information not found.",
                    code="subscription_missing",
                )

    return FeaturePermission
