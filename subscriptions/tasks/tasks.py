import logging

from celery import shared_task
from django.utils import timezone

from subscriptions.models import ShopSubscription, ShopFeatureSubscription

logger = logging.getLogger("api_requests")


@shared_task(name="subscriptions.tasks.expire_shop_subscriptions")
def expire_shop_subscriptions():
    """Bulk-mark ShopSubscriptions as EXPIRED when end_date has passed."""
    now = timezone.now()

    expired_qs = ShopSubscription.objects.filter(
        end_date__lte=now,
    )

    count = expired_qs.update(status="EXPIRED")

    logger.info(
        f"[expire_shop_subscriptions] Marked {count} subscription(s) as EXPIRED "
        f"(as of {now.isoformat()})"
    )

    # Also bulk-mark ShopFeatureSubscriptions as inactive when end_date has passed
    expired_features_qs = ShopFeatureSubscription.objects.filter(
        is_active=True,
        end_date__lte=now,
    )
    feature_count = expired_features_qs.update(is_active=False)

    logger.info(
        f"[expire_shop_subscriptions] Marked {feature_count} feature subscription(s) as inactive "
        f"(as of {now.isoformat()})"
    )

    return {"expired_count": count, "expired_feature_count": feature_count}
