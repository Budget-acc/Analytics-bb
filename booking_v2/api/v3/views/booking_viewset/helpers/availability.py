from datetime import timedelta
from booking_v2.models import BookingV2, BookedProductVariant, ProductVariantAvailability
from services.models import ProductVariant
from django.db.models import Sum, F, Case, When
from django.db import models
import logging

logger = logging.getLogger("api_requests")


def get_variants_availability(
    self,
    variant_ids,
    event_date,
    return_date,
    event_datetime=None,
    return_datetime=None,
    booking_id=None,
):
    """
    Returns availability for given variant_ids.
    """

    # Initialize result with defaults for ALL requested variant IDs
    result = {}
    for vid in variant_ids:
        result[vid] = {
            "variant_id": vid,
            "stock": 0,
            "remaining_stock": 0,
            "is_available": False,
            "found": False,
            "error": None
        }

    try:
        # Fetch variants with error handling
        variants = ProductVariant.objects.filter(
            id__in=variant_ids,
            is_active=True,
            deleted__isnull=True
        ).select_related("product")

        variant_map = {v.id: v for v in variants}

        # Update result for variants that exist
        for vid, variant in variant_map.items():
            result[vid].update({
                "stock": variant.stock,
                "found": True
            })

        if not variant_map:
            return result  # Return defaults for all requested IDs

        # Editing Booking Handling
        editing_booking = None
        editing_booked_variants = {}

        if booking_id:
            try:
                editing_booking = BookingV2.objects.filter(id=booking_id).first()

                # Explicitly handle case where booking is not found
                if not editing_booking:
                    logger.warning(f"Booking ID {booking_id} not found for availability check")
                    # Continue without editing adjustment - booking doesn't exist
                else:
                    booked_items = BookedProductVariant.objects.filter(
                        booking_id=booking_id,
                        deleted__isnull=True
                    )

                    for item in booked_items:
                        if item.product_variant_id:
                            editing_booked_variants[item.product_variant_id] = item.quantity or 1

            except Exception as e:
                logger.error(f"Error fetching booking {booking_id} for availability check: {str(e)}", exc_info=True)
                editing_booking = None
                editing_booked_variants = {}

        # TIMESTAMP BASED CHECK (with cooling period handling)
        if event_datetime and return_datetime:
            try:
                overlapping_qs = BookedProductVariant.objects.filter(
                    product_variant_id__in=variant_ids,
                    booking__deleted__isnull=True,
                    booking__booking_status="upcoming",
                    deleted__isnull=True
                )

                if editing_booking:
                    overlapping_qs = overlapping_qs.exclude(
                        booking_id=editing_booking.id
                    )

                # Handle cooling periods properly
                overlapping_qs = overlapping_qs.annotate(
                    effective_start=Case(
                        When(
                            booking__cooling_period_end__isnull=False,
                            booking__cooling_period_end__lt=F("booking__pickup_date"),
                            then=F("booking__cooling_period_end"),
                        ),
                        default=F("booking__pickup_date"),
                        output_field=models.DateTimeField(),
                    ),
                    effective_end=Case(
                        When(
                            booking__cooling_period_end__isnull=False,
                            booking__cooling_period_end__gt=F("booking__return_date"),
                            then=F("booking__cooling_period_end"),
                        ),
                        default=F("booking__return_date"),
                        output_field=models.DateTimeField(),
                    ),
                ).filter(
                    effective_start__lt=return_datetime,
                    effective_end__gt=event_datetime,
                ).values("product_variant_id").annotate(total=Sum("quantity"))

                overlap_map = {
                    row["product_variant_id"]: row["total"]
                    for row in overlapping_qs
                }

                # Update result with actual availability
                for vid, variant in variant_map.items():
                    booked = overlap_map.get(vid, 0)
                    remaining = max(0, variant.stock - booked)
                    result[vid].update({
                        "remaining_stock": remaining,
                        "is_available": remaining > 0,
                    })

            except Exception as e:
                logger.error(f"Error in timestamp-based availability check: {str(e)}", exc_info=True)
                # Mark variants with error but keep existing data
                for vid in variant_map.keys():
                    result[vid]["error"] = "Failed to check timestamp-based availability"

            return result

        # DATE BASED CHECK
        try:
            date_range = [
                event_date + timedelta(days=i)
                for i in range((return_date - event_date).days + 1)
            ]

            availabilities = ProductVariantAvailability.objects.filter(
                variant_id__in=variant_ids,
                date__in=date_range
            ).values("variant_id", "date", "booked_quantity")

            availability_map = {}

            for row in availabilities:
                availability_map.setdefault(row["variant_id"], {})
                availability_map[row["variant_id"]][row["date"]] = row["booked_quantity"]

            # editing adjustment
            if editing_booking:
                # Get original booking dates including cooling period
                original_start = editing_booking.pickup_date.date()
                original_end = editing_booking.return_date.date() if editing_booking.return_date else original_start

                # Adjust for cooling period if present
                if editing_booking.cooling_period_end:
                    cooling_date = editing_booking.cooling_period_end.date()
                    if cooling_date < original_start:
                        original_start = cooling_date
                    elif cooling_date > original_end:
                        original_end = cooling_date

                # Generate original date range
                original_dates = [
                    original_start + timedelta(days=i)
                    for i in range((original_end - original_start).days + 1)
                ]

                for vid, qty in editing_booked_variants.items():
                    if vid not in availability_map:
                        availability_map[vid] = {}

                    for d in date_range:
                        if d in original_dates:
                            current_booked = availability_map[vid].get(d, 0)
                            availability_map[vid][d] = max(0, current_booked - qty)

            # Update result with actual availability
            for vid, variant in variant_map.items():
                remaining_list = []

                for d in date_range:
                    booked = availability_map.get(vid, {}).get(d, 0)
                    remaining_list.append(variant.stock - booked)

                remaining = min(remaining_list) if remaining_list else variant.stock
                remaining = max(0, remaining)

                result[vid].update({
                    "remaining_stock": remaining,
                    "is_available": remaining > 0,
                })

        except Exception as e:
            logger.error(f"Error in date-based availability check: {str(e)}", exc_info=True)
            # Mark variants with error but keep existing data
            for vid in variant_map.keys():
                result[vid]["error"] = "Failed to check date-based availability"

        return result

    except Exception as e:
        logger.error(f"Unexpected error in get_variants_availability: {str(e)}", exc_info=True)
        # Return result with error flags
        for vid in result:
            result[vid]["error"] = "Internal server error during availability check"
        return result
