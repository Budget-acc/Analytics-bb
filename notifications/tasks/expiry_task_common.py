import logging
from datetime import datetime
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from notifications.api.v3.utils.notification_service import create_shop_notification
from notifications.models import Notification
from services.models import ServiceAttributeValue

logger = logging.getLogger("api_requests")

EXPIRY_REMINDER_DAYS = 7


def _parse_expiry_date(raw_value, fallback_key):
    """Parse expiry date from JSONField payloads with tolerant formats."""
    candidate = raw_value

    if isinstance(raw_value, dict):
        candidate = (
            raw_value.get("expiry_date")
            or raw_value.get("date")
            or raw_value.get(fallback_key)
            or raw_value.get("value")
        )

    if not candidate:
        return None

    candidate = str(candidate).strip()
    if not candidate:
        return None

    # First try ISO-like parsing.
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    # Fallback common formats.
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue

    return None


def _delete_existing_expiry_notifications(*, shop, content_type, object_id, expiry_type):
    """
    Hard-delete all existing Notification rows for this expiry event before creating a new one.
    UserNotification rows are removed automatically via CASCADE.
    Uses select_for_update() to serialize concurrent task workers and prevent duplicate creates.
    Must be called inside a transaction.atomic() block.
    """
    ids = list(
        Notification.objects.select_for_update().filter(
            shop=shop,
            notification_type="expiry",
            content_type=content_type,
            object_id=object_id,
            data__expiry_type=expiry_type,
        ).values_list("id", flat=True)
    )
    if not ids:
        return 0
    deleted_count, _ = Notification.objects.filter(id__in=ids).delete()
    return deleted_count


def execute_expiry_notification_task(*, expiry_attr_key, expiry_type, title_prefix):
    """
    Generic executor used by individual expiry tasks.

    Sends reminders for [today .. today+7] window and keeps only latest
    reminder visible for each (expiry_type + object) pair.
    """
    today = timezone.now().date()
    window_end = today + timedelta(days=EXPIRY_REMINDER_DAYS)

    queryset = (
        ServiceAttributeValue.objects
        .filter(
            attribute__attribute_key=expiry_attr_key,
            value__isnull=False,
        )
        .select_related(
            "product__shop_service__shop",
            "variant__product__shop_service__shop",
        )
        .only(
            "id",
            "value",
            "product__id",
            "product__name",
            "product__image",
            "product__shop_service__shop__id",
            "product__shop_service__shop__name",
            "product__shop_service__shop__is_active",
            "variant__id",
            "variant__product__id",
            "variant__product__name",
            "variant__product__image",
            "variant__product__shop_service__shop__id",
            "variant__product__shop_service__shop__name",
            "variant__product__shop_service__shop__is_active",
        )
    )

    stats = {
        "processed": 0,
        "created": 0,
        "failed": 0,
        "invalid_date": 0,
        "out_of_window": 0,
        "inactive_shop": 0,
        "no_target": 0,
    }

    logger.info("Starting expiry task: %s", expiry_type)

    for attribute_value in queryset.iterator(chunk_size=250):
        stats["processed"] += 1

        target_variant = attribute_value.variant
        target_product = attribute_value.product or (
            target_variant.product if target_variant and target_variant.product_id else None
        )

        if not target_product:
            stats["no_target"] += 1
            continue

        shop = target_product.shop_service.shop
        if not shop or not shop.is_active:
            stats["inactive_shop"] += 1
            continue

        expiry_date = _parse_expiry_date(attribute_value.value, expiry_attr_key)
        if not expiry_date:
            stats["invalid_date"] += 1
            continue

        days_left = (expiry_date - today).days
        if days_left < 0 or days_left > EXPIRY_REMINDER_DAYS:
            stats["out_of_window"] += 1
            continue

        title = f"{title_prefix} Reminder"
        body = (
            f"{target_product.name} {expiry_type.replace('_expiry', '').replace('_', ' ').strip().title()} expires in "
            f"{days_left} day{'s' if days_left != 1 else ''}."
        )

        content_object = target_variant or target_product
        object_id = content_object.id
        content_type = ContentType.objects.get_for_model(content_object)

        data = {
            "redirect_to": "PRODUCT_DETAIL",
            "product_id": str(target_product.id),
            "product_name": target_product.name or "",
            "product_image": target_product.image or "",
            "product_variant_id": str(target_variant.id) if target_variant else "",
            "shop_service_id": str(target_product.shop_service_id),
        }

        try:
            with transaction.atomic():
                # Remove the previous day's notification for this expiry event FIRST,
                # so only the latest one ever exists in the DB.
                _delete_existing_expiry_notifications(
                    shop=shop,
                    content_type=content_type,
                    object_id=object_id,
                    expiry_type=expiry_type,
                )

                notification = create_shop_notification(
                    shop=shop,
                    title=title,
                    body=body,
                    notification_type="expiry",
                    priority="high" if days_left <= 2 else "normal",
                    data=data,
                    image_url=target_product.image,
                    content_object=content_object,
                    send_push=True,
                )

                if not notification:
                    stats["failed"] += 1
                    continue

            stats["created"] += 1

        except Exception:
            stats["failed"] += 1
            logger.exception(
                "Failed %s expiry notification for attribute_value=%s",
                expiry_type,
                attribute_value.id,
            )

    logger.info("Expiry task %s complete: %s", expiry_type, stats)
    return stats
