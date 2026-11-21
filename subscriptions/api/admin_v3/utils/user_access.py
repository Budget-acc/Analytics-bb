# user_access.py
from subscriptions.models import UserFeature, ShopUserFeature
from django.conf import settings

def has_user_feature_access(shop_user, feature_code):
    """
    Check if a shop user has access to a specific feature.

    Logic:
    1. Check ShopUserFeature for explicit grant/deny
    2. If no explicit override, check UserFeature based on role
    3. Return True only if explicitly granted or role has it by default
    """
    if getattr(settings, "ANALYTICS_MODE", False):
        return True
    # 1. First check for explicit ShopUserFeature override
    try:
        shop_user_feature = ShopUserFeature.objects.get(
            shop_user=shop_user,
            feature__code=feature_code
        )
        # Explicit override found, return the granted status
        return shop_user_feature.granted
    except ShopUserFeature.DoesNotExist:
        # No explicit override, check UserFeature based on role
        pass

    # 2. Check if feature exists for user's role in UserFeature
    has_role_feature = UserFeature.objects.filter(
        role=shop_user.role,
        feature__code=feature_code
    ).exists()

    return has_role_feature

def get_user_accessible_features(shop_user):
    """
    Get all features accessible to a shop user.
    Returns a set of feature codes.
    """
    if getattr(settings, "ANALYTICS_MODE", False):
        from subscriptions.models import Feature
        return set(Feature.objects.values_list("code", flat=True))


    accessible_features = set()

    # 1. Get features from ShopUserFeature (explicit grants)
    granted_features = ShopUserFeature.objects.filter(
        shop_user=shop_user,
        granted=True
    ).values_list('feature__code', flat=True)

    accessible_features.update(granted_features)

    # 2. Get features from UserFeature based on role (excluding denied ones)
    role_features = UserFeature.objects.filter(
        role=shop_user.role
    ).values_list('feature__code', flat=True)

    # 3. Get denied features from ShopUserFeature
    denied_features = ShopUserFeature.objects.filter(
        shop_user=shop_user,
        granted=False
    ).values_list('feature__code', flat=True)

    # Add role features except denied ones
    accessible_features.update(role_features)
    accessible_features.difference_update(denied_features)

    return accessible_features
