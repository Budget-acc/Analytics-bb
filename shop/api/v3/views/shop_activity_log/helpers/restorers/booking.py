from datetime import datetime, date
from django.db import transaction
from django.utils import timezone
from booking_v2.models import BookingV2, BookedProductVariant, PaymentDetailsV2, ClientRequirementV2
from booking_v2.api.v5.utils.availability import (
    check_product_variant_availability,
    add_product_variant_availability,
)
from shop.models import ShopActivityLog
import logging

logger = logging.getLogger(__name__)


def restore_booking(viewset, log, request):
    """
    Restore a soft-deleted BookingV2.

    Effective date range is derived by direct date comparison (date-level, time ignored):
      - cooling strictly before pickup  → effective_start = cooling_period_end, end = return_date
      - cooling strictly after return   → effective_start = pickup_date,         end = cooling_period_end
      - cooling same date as pickup/return, or between → no range extension (use pickup/return)
      - no cooling period               → effective_start = pickup_date,         end = return_date

    Conflict check is skipped only when stock_end_date is entirely in the past.
    add_product_variant_availability uses the original effective_start (not the
    today-shifted check_start) so the full booking period is tracked in stock.
    Hard-deleted records (SecurityDepositPayment, RefundDetails, BookingAdditionalCharge,
    SecurityDepositAdjustment) cannot be recovered.
    """
    shop_id = viewset.shop_id
    booking_id = log.object_id

    # Fetch the soft-deleted booking (must belong to this shop)
    booking = (
        BookingV2.all_objects
        .filter(id=booking_id, shop_id=shop_id, deleted__isnull=False)
        .select_related("shop")
        .first()
    )
    if not booking:
        return viewset.not_found_response(
            resource="Booking",
            message="Deleted booking not found or already restored."
        )

    today = date.today()
    pickup_date = booking.pickup_date          # timezone-aware datetime from DB
    return_date = booking.return_date          # timezone-aware datetime from DB
    cooling_period_end = booking.cooling_period_end  # timezone-aware datetime or None

    # Derive effective date range from date values only — no cooling_period_type dependency.
    # Cooling extends the range ONLY when it strictly falls before pickup or after return
    # (date-level comparison; time is irrelevant). When the cooling date equals the pickup
    # or return date, the booking dates already cover the window — no extension needed.
    if not cooling_period_end:
        effective_start = pickup_date
        stock_end_date = return_date
    elif cooling_period_end.date() < pickup_date.date():
        # Cooling strictly before pickup — effective window starts at cooling
        effective_start = cooling_period_end
        stock_end_date = return_date
    elif cooling_period_end.date() > return_date.date():
        # Cooling strictly after return — effective window ends at cooling
        effective_start = pickup_date
        stock_end_date = cooling_period_end
    else:
        # Same date as pickup or return, or between — cooling doesn't extend the range
        effective_start = pickup_date
        stock_end_date = return_date

    effective_end_date = stock_end_date.date() if isinstance(stock_end_date, datetime) else stock_end_date
    needs_conflict_check = effective_end_date >= today

    # Preserve original effective_start for availability add (must not use shifted check_start)
    check_start = effective_start

    if needs_conflict_check:
        booked_items = BookedProductVariant.all_objects.filter(booking_id=booking.id)
        variants = [
            {"id": item.product_variant_id, "quantity": item.quantity}
            for item in booked_items
        ]

        # Shift check_start to today if already in the past (mirrors _validate_variants in create flow)
        check_date = check_start.date() if isinstance(check_start, datetime) else check_start
        if check_date < today:
            if isinstance(check_start, datetime):
                check_start = timezone.make_aware(
                    datetime.combine(today, check_start.time())
                )
            else:
                check_start = today

        conflict = check_product_variant_availability(variants, check_start, stock_end_date)
        if conflict:
            return conflict  # Same 400 response shape as booking creation

    try:
        with transaction.atomic():
            # Defensive guard: if called outside restore() entrypoint, avoid double restore logs.
            if ShopActivityLog.objects.filter(restored_from_activity_id=log.id).exists():
                return viewset.validation_error_response(
                    errors={"log": ["This record has already been restored."]},
                    message="This record has already been restored."
                )

            # Undelete booking
            booking.deleted = None
            booking.save(update_fields=["deleted"])

            # Undelete soft-deleted children (CASCADE-deleted alongside booking)
            BookedProductVariant.all_objects.filter(
                booking_id=booking.id, deleted__isnull=False
            ).update(deleted=None)
            PaymentDetailsV2.all_objects.filter(
                booking_id=booking.id, deleted__isnull=False
            ).update(deleted=None)
            ClientRequirementV2.all_objects.filter(
                booking_id=booking.id, deleted__isnull=False
            ).update(deleted=None)

            # Re-add availability using original effective_start (not the shifted check_start)
            # so the full booking period is tracked, including any past dates.
            if needs_conflict_check:
                restored_items = BookedProductVariant.objects.filter(booking_id=booking.id)
                variants = [
                    {"id": item.product_variant_id, "quantity": item.quantity}
                    for item in restored_items
                ]
                add_product_variant_availability(
                    variants,
                    effective_start.date() if isinstance(effective_start, datetime) else effective_start,
                    effective_end_date,
                )

            # Create restore activity log linked back to the original delete log
            ShopActivityLog.objects.create(
                shop=booking.shop,
                user=request.user,
                content_type=log.content_type,
                object_id=booking.id,
                action="restored",
                log_message=f"Booking #{booking.shop_booking_id} restored.",
                restored_from_activity=log,
            )

        return viewset.success_response(
            data={"booking_id": booking.id},
            message="Booking restored successfully."
        )

    except Exception as e:
        logger.error(f"Failed to restore booking {booking_id}: {e}", exc_info=True)
        return viewset.error_response(
            message="Unable to restore booking. Please try again later.",
            dev_message=str(e)
        )
