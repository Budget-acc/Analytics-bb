"""
Centralized notification creation service.

Creates Notification + UserNotification records and dispatches FCM push
in a single atomic flow. Every module (bookings, sales, tailored, stock, etc.)
should use this service to generate notifications.
"""

import logging
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from notifications.models import Notification, UserNotification
from shop.models import ShopUser

logger = logging.getLogger("api_requests")


def create_shop_notification(
    *,
    shop,
    title,
    body,
    notification_type="system",
    priority="normal",
    data=None,
    image_url=None,
    content_object=None,
    created_by=None,
    target="shop",
    exclude_user_ids=None,
    send_push=True,
):
    """
    Create a Notification + per-user UserNotification records for all active
    shop members, then dispatch FCM push notifications.

    Args:
        shop:               Shop instance
        title:              Notification title
        body:               Notification body text
        notification_type:  One of Notification.TYPE_CHOICES keys
        priority:           low / normal / high
        data:               Extra JSON payload for the frontend
        image_url:          Optional image URL
        content_object:     Optional Django model instance for generic FK
        created_by:         User who triggered the event (optional)
        target:             Notification target scope
        exclude_user_ids:   Set/list of user IDs to skip (e.g. the actor)
        send_push:          Whether to dispatch FCM push after DB writes

    Returns:
        Notification instance (or None on failure)
    """
    if not shop or not title:
        logger.warning("create_shop_notification called with missing shop or title")
        return None

    exclude_user_ids = set(exclude_user_ids or [])

    try:
        with transaction.atomic():
            # --- Build content_type / object_id for generic FK ---
            ct, obj_id = None, None
            if content_object is not None:
                ct = ContentType.objects.get_for_model(content_object)
                obj_id = content_object.pk

            # --- Create root Notification ---
            notification = Notification.objects.create(
                title=title,
                body=body,
                data=data or {},
                image_url=image_url,
                shop=shop,
                notification_type=notification_type,
                priority=priority,
                content_type=ct,
                object_id=obj_id,
                created_by=created_by,
                target=target,
                sent_at=timezone.now(),
            )

            # --- Find eligible shop users ---
            shop_user_qs = (
                ShopUser.objects
                .filter(shop=shop, is_active=True, deleted__isnull=True)
                .select_related("user")
            )

            if exclude_user_ids:
                shop_user_qs = shop_user_qs.exclude(user_id__in=exclude_user_ids)

            user_notifications = [
                UserNotification(
                    user=su.user,
                    notification=notification,
                    shop=shop,
                    push_status="pending",
                )
                for su in shop_user_qs
            ]

            if user_notifications:
                UserNotification.objects.bulk_create(
                    user_notifications, ignore_conflicts=True
                )

        # --- Dispatch FCM push after the outermost transaction commits ---
        # Note: push delivery depends on the shop's DeviceToken rows, not on
        # whether any ShopUser/UserNotification rows were created above (those
        # are two independent concerns - in-app notification list vs push
        # delivery) - so this must not be gated on `user_notifications`.
        if send_push:
            _n, _s, _d, _i = notification, shop, data, image_url
            transaction.on_commit(lambda: _dispatch_push(
                notification=_n, shop=_s, data=_d, image_url=_i,
            ))

        logger.info(
            "Notification created id=%s shop=%s type=%s users=%d",
            notification.id, shop.id, notification_type, len(user_notifications),
        )
        return notification

    except Exception:
        logger.exception("Failed to create shop notification for shop=%s", shop.id)
        return None


def create_user_notification(
    *,
    user,
    shop,
    title,
    body,
    notification_type="system",
    priority="normal",
    data=None,
    image_url=None,
    content_object=None,
    created_by=None,
    send_push=True,
):
    """
    Create a Notification targeted at a single user.
    """
    if not user or not title:
        return None

    try:
        with transaction.atomic():
            ct, obj_id = None, None
            if content_object is not None:
                ct = ContentType.objects.get_for_model(content_object)
                obj_id = content_object.pk

            notification = Notification.objects.create(
                title=title,
                body=body,
                data=data or {},
                image_url=image_url,
                shop=shop,
                notification_type=notification_type,
                priority=priority,
                content_type=ct,
                object_id=obj_id,
                created_by=created_by,
                target="user",
                sent_at=timezone.now(),
            )

            UserNotification.objects.create(
                user=user,
                notification=notification,
                shop=shop,
                push_status="pending",
            )

        if send_push:
            _n, _u, _s, _d, _i = notification, user, shop, data, image_url
            transaction.on_commit(lambda: _dispatch_push_for_user(
                notification=_n, user_id=_u.id, shop=_s, data=_d, image_url=_i,
            ))

        return notification

    except Exception:
        logger.exception("Failed to create user notification user=%s", user.id)
        return None


def cleanup_duplicate_expiry_notifications(
    *,
    shop,
    notification_type,
    object_id,
    content_type,
    keep_latest_id,
):
    """
    Soft-delete older expiry notifications for the same object, keeping only
    the latest one. This ensures the notification list shows only the most
    recent reminder per expiry record.
    """
    if not all([shop, notification_type, object_id, content_type, keep_latest_id]):
        return 0

    old_notifications = Notification.objects.filter(
        shop=shop,
        notification_type=notification_type,
        content_type=content_type,
        object_id=object_id,
    ).exclude(id=keep_latest_id)

    old_ids = list(old_notifications.values_list("id", flat=True))

    if not old_ids:
        return 0

    deleted_count = UserNotification.objects.filter(
        notification_id__in=old_ids
    ).update(is_deleted=True)

    logger.info(
        "Cleaned up %d duplicate expiry UserNotifications for object=%s type=%s",
        deleted_count, object_id, notification_type,
    )
    return deleted_count


# ---------------------------------------------------------------------------
# Internal push dispatch helpers
# ---------------------------------------------------------------------------

def _dispatch_push(*, notification, shop, data, image_url):
    """Send FCM push to all active tokens for the given shop."""
    try:
        from notifications.api.v3.utils.firebase_utils import send_shop_notification

        push_data = _build_push_data(notification, data)

        result = send_shop_notification(
            shop_id=shop.id,
            title=notification.title,
            body=notification.body,
            data=push_data,
            image=image_url,
        )

        if result:
            _update_push_status(notification.id, shop.id, result)
        else:
            # send_shop_notification returned None (e.g. no active device
            # tokens, or an internal send failure) - mark as failed instead
            # of leaving rows stuck on "pending" forever.
            UserNotification.objects.filter(
                notification_id=notification.id, shop_id=shop.id
            ).update(push_status="failed")

    except Exception:
        logger.exception("FCM push failed for notification=%s", notification.id)
        UserNotification.objects.filter(
            notification_id=notification.id, shop_id=shop.id
        ).update(push_status="failed")


def _dispatch_push_for_user(*, notification, user_id, shop, data, image_url):
    """Send FCM push to a single user's active tokens."""
    try:
        from notifications.api.v3.utils.firebase_utils import send_user_notification as send_fcm

        push_data = _build_push_data(notification, data)

        result = send_fcm(
            user_id=user_id,
            title=notification.title,
            body=notification.body,
            data=push_data,
            image=image_url,
            shop_id=shop.id if shop else None,
        )

        if result:
            success = result.get("success_count", 0) > 0
            UserNotification.objects.filter(
                notification=notification, user_id=user_id
            ).update(push_status="sent" if success else "failed")

    except Exception:
        logger.exception("FCM push failed for user=%s notification=%s", user_id, notification.id)


def _build_push_data(notification, extra_data):
    """Build the FCM data payload as string-keyed dict."""
    push_data = {
        "notification_id": str(notification.id),
        "type": notification.notification_type or "",
        "priority": notification.priority or "normal",
    }
    if notification.object_id:
        push_data["object_id"] = str(notification.object_id)
    if notification.content_type_id:
        push_data["content_type"] = str(notification.content_type.model)
    if extra_data and isinstance(extra_data, dict):
        for k, v in extra_data.items():
            push_data[str(k)] = "" if v is None else str(v)
    return push_data


def _update_push_status(notification_id, shop_id, result):
    """Bulk-update push_status for UserNotifications after FCM send."""
    success = result.get("success_count", 0) > 0
    new_status = "sent" if success else "failed"
    UserNotification.objects.filter(
        notification_id=notification_id, shop_id=shop_id
    ).update(push_status=new_status)
