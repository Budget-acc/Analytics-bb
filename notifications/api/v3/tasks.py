import logging
from celery import shared_task
from .utils.firebase_utils import send_to_tokens, send_targeted_notification, send_shop_notification, get_shop_token_count
from datetime import date,time
from firebase_admin import messaging
from booking_v2.models import BookingV2
from shop.models import Shop
from datetime import datetime, timedelta
from django.utils.timezone import make_aware
from django.utils import timezone
from django.db.models import Count
from tailored.models import TailorOrderItem


logger = logging.getLogger(__name__)

BATCH_SIZE = 400  

def send_batch_notifications(tokens, title, body, data=None, image=None):
    """Send notifications in batches using per-token sender."""
    for i in range(0, len(tokens), BATCH_SIZE):
        batch_tokens = tokens[i:i + BATCH_SIZE]
        send_to_tokens(batch_tokens, title, body, data=data, image=image)


@shared_task
def notify_pickup(booking_id):
    try:
        booking = BookingV2.objects.get(id=booking_id)
    except BookingV2.DoesNotExist:
        logger.warning(f"Booking {booking_id} not found for pickup notification")
        return

    shop_users = booking.shop.shop_users.filter(is_active=True).select_related("user")
    for shop_user in shop_users:
        tokens = list(shop_user.user.device_tokens.filter(is_active=True)
                      .values_list("token", flat=True))
        if tokens:
            send_batch_notifications(
                tokens,
                "Booking Pickup Reminder",
                f"Booking #{booking.shop_booking_id} is scheduled for pickup today!"
            )


@shared_task
def notify_return(booking_id):
    try:
        booking = BookingV2.objects.get(id=booking_id)
    except BookingV2.DoesNotExist:
        logger.warning(f"Booking {booking_id} not found for return notification")
        return

    shop_users = booking.shop.shop_users.filter(is_active=True).select_related("user")
    for shop_user in shop_users:
        tokens = list(shop_user.user.device_tokens.filter(is_active=True)
                      .values_list("token", flat=True))
        if tokens:
            send_batch_notifications(
                tokens,
                "Booking Return Reminder",
                f"Booking #{booking.shop_booking_id} is due for return today!"
            )

@shared_task
def notify_daily_returns():
    """Send evening notification with total returns due today for each shop."""
    today_start = make_aware(datetime.combine(datetime.today(), datetime.min.time()))
    today_end = make_aware(datetime.combine(datetime.today(), datetime.max.time()))

    # Step 1: Aggregate return counts grouped by shop
    return_counts = (
        BookingV2.objects.filter(return_date__range=(today_start, today_end))
        .values("shop_id")
        .annotate(total_returns=Count("id"))
    )

    shop_returns_map = {rc["shop_id"]: rc["total_returns"] for rc in return_counts}

    # Step 2: Loop through active shops only once
    for shop in Shop.objects.filter(is_active=True):
        total_returns = shop_returns_map.get(shop.id, 0)

        if total_returns > 0:
            logger.info(f"Shop {shop.id} - Total returns today: {total_returns}")

            # Step 3: Use firebase_utils helper (optimized token fetch inside)
            send_targeted_notification(
                title="Reminder! Today's Returns ↩️",
                body=f"Hello! You have {total_returns} item{'s' if total_returns != 1 else ''} scheduled for return today.",
                target="shop",
                shop_id=shop.id,
                data={"redirect_to": "UPCOMING"}
            )



# Updated task with better error handling and debugging
@shared_task
def notify_daily_bookings():
    """
    Send morning notification with total bookings of the day for each shop.
    Enhanced with debugging and error handling.
    """
    from shop.models import Shop
    from booking_v2.models import BookingV2
    
    today_start = make_aware(datetime.combine(datetime.today(), datetime.min.time()))
    today_end = make_aware(datetime.combine(datetime.today(), datetime.max.time()))

    logger.info("=== STARTING DAILY BOOKING NOTIFICATIONS ===")

    # Step 1: Aggregate booking counts grouped by shop
    booking_counts = (
        BookingV2.objects.filter(pickup_date__range=(today_start, today_end))
        .values("shop_id")
        .annotate(total_bookings=Count("id"))
    )

    # Step 2: Create a mapping for faster lookup
    shop_bookings_map = {bc["shop_id"]: bc["total_bookings"] for bc in booking_counts}
    logger.info(f"Found bookings for {len(shop_bookings_map)} shops today")

    # Step 3: Loop through active shops and send notifications if bookings exist
    successful_shops = 0
    failed_shops = 0
    
    for shop in Shop.objects.filter(is_active=True):
        total_bookings = shop_bookings_map.get(shop.id, 0)

        if total_bookings > 0:
            # Check if shop has any active device tokens before sending
            token_count = get_shop_token_count(shop.id)
            
            if token_count > 0:
                logger.info(f"Processing Shop {shop.id} ({shop.name}): {total_bookings} bookings, {token_count} active tokens")

                try:
                    # Use the new shop-based notification function
                    result = send_shop_notification(
                        shop_id=shop.id,
                        title="Good morning! Today's Bookings 🗓️",
                        body=f"You have {total_bookings} pickup{'s' if total_bookings != 1 else ''} today. Have a great day!",
                        data={
                            "redirect_to": "HOMESCREEN",
                            "booking_count": str(total_bookings),
                            "date": datetime.today().strftime("%Y-%m-%d"),
                            "shop_id": str(shop.id)
                        }
                    )
                    
                    if result and result.get('success_count', 0) > 0:
                        logger.info(f"✓ Shop {shop.id}: Sent {result.get('success_count', 0)}/{token_count} notifications successfully")
                        successful_shops += 1
                    else:
                        logger.warning(f"✗ Shop {shop.id}: Failed to send notifications")
                        failed_shops += 1
                        
                except Exception as e:
                    logger.error(f"✗ Shop {shop.id}: Exception while sending notifications - {e}")
                    failed_shops += 1
                    
            else:
                logger.debug(f"Shop {shop.id} ({shop.name}): No active device tokens, skipping")
        else:
            logger.debug(f"Shop {shop.id} ({shop.name}): No bookings today, skipping")
    
    logger.info(f"=== DAILY BOOKING NOTIFICATIONS COMPLETE: {successful_shops} successful, {failed_shops} failed ===")
    
    return {
        "successful_shops": successful_shops,
        "failed_shops": failed_shops,
        "total_shops_with_bookings": len([s for s in shop_bookings_map.values() if s > 0])
    }

@shared_task
def notify_tailor_pickups():
    """
    Send morning notification to shops with tailor pickups scheduled for today.
    Optimized to iterate only over shops that have tailor pickups today.
    """
    from shop.models import Shop
    from tailored.models import TailorOrderItem  

    today_start = make_aware(datetime.combine(datetime.today(), datetime.min.time()))
    today_end = make_aware(datetime.combine(datetime.today(), datetime.max.time()))

    logger.info("=== STARTING DAILY TAILOR PICKUP NOTIFICATIONS ===")

    # Step 1: Aggregate tailor pickups per shop
    tailor_counts = (
        TailorOrderItem.objects.filter(
            expected_completion_date__range=(today_start, today_end),
            assigned_tailor__isnull=False,
            order__shop__is_active=True,
        )
        .values("order__shop_id")
        .annotate(total_tailor_pickups=Count("id"))
    )

    if not tailor_counts:
        logger.info("No tailor pickups scheduled for today.")
        return {"successful_shops": 0, "failed_shops": 0, "total_shops_with_pickups": 0}

    # Step 2: Map shop_id → count
    shop_tailor_map = {
        entry["order__shop_id"]: entry["total_tailor_pickups"] for entry in tailor_counts
    }

    # Step 3: Fetch only shops that have tailor pickups today
    active_shops = Shop.objects.filter(id__in=shop_tailor_map.keys(), is_active=True)

    logger.info(f"Found {len(shop_tailor_map)} shops with tailor pickups today")

    successful_shops = 0
    failed_shops = 0

    # Step 4: Send notifications
    for shop in active_shops:
        total_pickups = shop_tailor_map.get(shop.id, 0)
        token_count = get_shop_token_count(shop.id)

        if token_count == 0:
            logger.debug(f"Shop {shop.id} ({shop.name}): No active device tokens, skipping")
            continue

        logger.info(f"Processing Shop {shop.id} ({shop.name}): {total_pickups} tailor pickups, {token_count} tokens")

        try:
            result = send_shop_notification(
                shop_id=shop.id,
                title="Tailor Pickup Reminder 👕",
                body=f"You have {total_pickups} tailor pickup{'s' if total_pickups != 1 else ''} scheduled for today.",
                data={
                    "redirect_to": "TAILOR_SCREEN",
                    "pickup_count": str(total_pickups),
                    "date": datetime.today().strftime("%Y-%m-%d"),
                    "shop_id": str(shop.id),
                },
            )

            if result and result.get("success_count", 0) > 0:
                logger.info(f"✓ Shop {shop.id}: Sent {result.get('success_count', 0)}/{token_count} notifications successfully")
                successful_shops += 1
            else:
                logger.warning(f"✗ Shop {shop.id}: Failed to send notifications")
                failed_shops += 1

        except Exception as e:
            logger.error(f"✗ Shop {shop.id}: Exception while sending notifications - {e}", exc_info=True)
            failed_shops += 1

    logger.info(f"=== DAILY TAILOR PICKUP NOTIFICATIONS COMPLETE: {successful_shops} successful, {failed_shops} failed ===")

    return {
        "successful_shops": successful_shops,
        "failed_shops": failed_shops,
        "total_shops_with_pickups": len(shop_tailor_map),
    }

