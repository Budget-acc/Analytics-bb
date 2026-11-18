from django.db.models import OuterRef, Q, Subquery, Sum
from django.utils import timezone


def get_feature_limit_status(shop_id, plan_id, feature_ids):
    """
    Returns {feature_id: bool} indicating whether the shop has exceeded its
    usage limit, for every feature in `feature_ids` that is usage-metered
    under `plan_id` (i.e. has a FeatureUsagePolicy row for that plan).

    Feature ids with no FeatureUsagePolicy are simply absent from the
    returned dict - callers should treat a missing key as "not applicable"
    (None), not False, since "no limit configured" and "limit not exceeded"
    are different states the frontend needs to distinguish.

    Effective limit = FeatureUsagePolicy.limit + sum(active ShopFeatureLimit.extra_limit).
    Usage = the most recent ShopFeatureUsage row by period_end.

    Exactly 3 queries total regardless of len(feature_ids) - no per-feature
    or per-shop query loop.
    """
    from .feature_usage_policy import FeatureUsagePolicy
    from .shop_feature_limit import ShopFeatureLimit
    from .shop_feature_usage import ShopFeatureUsage

    if not feature_ids:
        return {}

    policy_limits = dict(
        FeatureUsagePolicy.objects.filter(
            plan_id=plan_id, feature_id__in=feature_ids
        ).values_list("feature_id", "limit")
    )

    if not policy_limits:
        return {}

    metered_feature_ids = list(policy_limits.keys())

    now = timezone.now()
    extra_limits = dict(
        ShopFeatureLimit.objects.filter(
            shop_id=shop_id, feature_id__in=metered_feature_ids, is_active=True
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=now))
        .values("feature_id")
        .annotate(total_extra=Sum("extra_limit"))
        .values_list("feature_id", "total_extra")
    )

    latest_usage_id = (
        ShopFeatureUsage.objects.filter(
            shop_id=shop_id, feature_id=OuterRef("feature_id")
        )
        .order_by("-period_end", "-id")
        .values("id")[:1]
    )
    usage_by_feature = dict(
        ShopFeatureUsage.objects.filter(shop_id=shop_id, feature_id__in=metered_feature_ids)
        .filter(id=Subquery(latest_usage_id))
        .values_list("feature_id", "used")
    )

    result = {}
    for feature_id in metered_feature_ids:
        # Floored at 0: extra_limit can be negative (e.g. a correction/clawback),
        # so a large negative adjustment against a small plan limit shouldn't be
        # able to push the effective limit below zero.
        effective_limit = max(policy_limits[feature_id] + extra_limits.get(feature_id, 0), 0)
        used = usage_by_feature.get(feature_id, 0)
        result[feature_id] = used >= effective_limit

    return result


def has_feature_access(shop_user, feature_code, require_active_subscription=True, as_of=None):
    """Check if a shop user has access to a specific feature."""
    try:
        if not hasattr(shop_user.shop, 'subscription'):
            return False

        subscription = shop_user.shop.subscription
        if require_active_subscription and not subscription.is_active():
            return False

        as_of = as_of or timezone.now()

        if hasattr(shop_user, 'subscription_access'):
            user_features = shop_user.subscription_access.get_accessible_features(as_of=as_of)
        else:
            user_features = subscription.get_all_features(as_of=as_of)

        return any(f.code == feature_code for f in user_features)

    except Exception:
        return False


