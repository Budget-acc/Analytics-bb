import logging

from celery import shared_task
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from notifications.api.v3.utils.notification_service import create_shop_notification
from notifications.models import Notification
from subscriptions.models import ShopSubscription

logger = logging.getLogger("api_requests")

SUBSCRIPTION_EXPIRY_MILESTONE_DAYS = {30, 15, 5, 1, 0}
SUBSCRIPTION_EXPIRY_TYPE = "subscription_expiry"


def _delete_existing_subscription_expiry_notifications(*, shop, content_type, object_id):
    """
    Hard-delete any existing subscription-expiry Notification row for this shop
    before creating a new one, so only the latest reminder stays visible.
    Uses select_for_update() to serialize concurrent task workers.
    Must be called inside a transaction.atomic() block.
    """
    ids = list(
        Notification.objects.select_for_update().filter(
            shop=shop,
            notification_type="expiry",
            content_type=content_type,
            object_id=object_id,
            data__expiry_type=SUBSCRIPTION_EXPIRY_TYPE,
        ).values_list("id", flat=True)
    )
    if not ids:
        return 0
    deleted_count, _ = Notification.objects.filter(id__in=ids).delete()
    return deleted_count


@shared_task
def check_subscription_expiry_notifications():
    """
    Reminders for shop subscriptions at fixed milestones before expiry:
    30, 15, 5, and 1 day(s) remaining, plus the expiry day itself (0 days left).
    At most 5 notifications per subscription cycle.
    """
    today = timezone.now().date()

    queryset = (
        ShopSubscription.objects
        .filter(status="ACTIVE", end_date__isnull=False)
        .select_related("shop")
    )

    stats = {
        "processed": 0,
        "created": 0,
        "failed": 0,
        "out_of_window": 0,
        "inactive_shop": 0,
    }

    logger.info("Starting expiry task: %s", SUBSCRIPTION_EXPIRY_TYPE)

    for subscription in queryset.iterator(chunk_size=250):
        stats["processed"] += 1

        # Skip subscriptions already lapsed (still marked ACTIVE but end_date passed).
        if subscription.get_runtime_status() != "ACTIVE":
            stats["out_of_window"] += 1
            continue

        shop = subscription.shop
        if not shop or not shop.is_active:
            stats["inactive_shop"] += 1
            continue

        days_left = (subscription.end_date.date() - today).days
        if days_left not in SUBSCRIPTION_EXPIRY_MILESTONE_DAYS:
            stats["out_of_window"] += 1
            continue

        title = "Your plan is expiring soon!"
        if days_left == 0:
            body = "Your subscription expires today. Renew now to enjoy uninterrupted service!"
        elif days_left == 1:
            body = "Your subscription expires tomorrow. Renew now to enjoy uninterrupted service!"
        else:  # days_left in {30, 15, 5}
            body = f"Your subscription expires in {days_left} days. Renew now to enjoy uninterrupted service!"

        content_type = ContentType.objects.get_for_model(subscription)
        data = {
            "redirect_to": "SUBSCRIPTION_DETAIL",
            "subscription_id": str(subscription.id),
            "expiry_type": SUBSCRIPTION_EXPIRY_TYPE,
            "days_left": str(days_left),
        }

        try:
            with transaction.atomic():
                # Remove the previous day's reminder for this subscription FIRST,
                # so only the latest one ever exists in the DB.
                _delete_existing_subscription_expiry_notifications(
                    shop=shop,
                    content_type=content_type,
                    object_id=subscription.id,
                )

                notification = create_shop_notification(
                    shop=shop,
                    title=title,
                    body=body,
                    notification_type="expiry",
                    priority="high" if days_left <= 2 else "normal",
                    data=data,
                    content_object=subscription,
                    send_push=True,
                )

                if not notification:
                    stats["failed"] += 1
                    continue

            stats["created"] += 1

        except Exception:
            stats["failed"] += 1
            logger.exception(
                "Failed subscription expiry notification for subscription=%s",
                subscription.id,
            )

    logger.info("Expiry task %s complete: %s", SUBSCRIPTION_EXPIRY_TYPE, stats)
    return stats
