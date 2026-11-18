from datetime import timedelta
from django.db import models
from django.db.models import F, When, Case

from booking_v2.models import ProductVariantAvailability
from rest_framework.response import Response
from rest_framework import status
from services.models import ProductVariant
from django.utils.timezone import localtime
from booking_v2.models import BookedProductVariant
import logging

logger = logging.getLogger("api_requests")
logs = logging.getLogger(__name__)

 
def check_product_variant_availability(variants, pickup_datetime, return_datetime, exclude_booking_id=None):
    """
    Check availability for a list of product variants between pickup_datetime and return_datetime.
    Each variant dict must have 'id' and 'quantity'.
    """

    def format_datetime(dt):
        if dt.tzinfo is not None:
            return localtime(dt).strftime("%d-%m-%Y %H:%M:%S")
        return dt.strftime("%d-%m-%Y %H:%M:%S")

    for variant_info in variants:
        variant_id = variant_info["id"]
        quantity = variant_info["quantity"]

        # Fetch variant with related product
        variant = ProductVariant.objects.select_related("product").get(id=variant_id)
        stock = variant.stock
        product_name = variant.product.name
        attribute = variant.attribute or "default"

        # Fetch overlapping bookings 
        existing_bookings = (
            BookedProductVariant.objects.filter(
                product_variant=variant,
                booking__deleted__isnull=True,
                booking__booking_status="upcoming",
            )
            .exclude(
                booking__delivery_status="returned" 
            )            
            .annotate(
                # Determine effective start date
                effective_start=Case(
                    When(
                        booking__cooling_period_end__isnull=False,
                        booking__cooling_period_end__lt=F('booking__pickup_date'),
                        then=F('booking__cooling_period_end')
                    ),
                    default=F('booking__pickup_date'),
                    output_field=models.DateTimeField()
                ),
                # Determine effective end date
                effective_end=Case(
                    When(
                        booking__cooling_period_end__isnull=False,
                        booking__cooling_period_end__gt=F('booking__return_date'),
                        then=F('booking__cooling_period_end')
                    ),
                    default=F('booking__return_date'),
                    output_field=models.DateTimeField()
                )
            )
            .filter(
                effective_start__lt=return_datetime,
                effective_end__gt=pickup_datetime
            )
            .select_related("booking")
        )

        # Exclude current booking if provided
        if exclude_booking_id:
            existing_bookings = existing_bookings.exclude(booking_id=exclude_booking_id)

        # Sum of quantities
        # Use peak concurrent overlap, not total range sum.
        # This prevents false positives when two bookings overlap different
        # sub-ranges of the request window but are not simultaneous.
        events = []
        booking_lookup = {}

        for row in existing_bookings:
            qty = row.quantity or 1
            start = row.effective_start
            end = row.effective_end

            # Clip booking interval to requested interval
            clip_start = max(start, pickup_datetime)
            clip_end = min(end, return_datetime)

            # Half-open interval [clip_start, clip_end)
            if clip_start >= clip_end:
                continue

            key = (row.booking_id, row.id)
            booking_lookup[key] = row

            # Process end events before start events at same timestamp
            # so adjacent windows are not treated as overlapping.
            events.append((clip_start, 1, qty, key))  # start
            events.append((clip_end, 0, qty, key))    # end

        events.sort(key=lambda x: (x[0], x[1]))

        current = 0
        peak_existing = 0
        active_keys = set()
        peak_active_keys = set()

        for _, kind, qty, key in events:
            if kind == 0:
                # end
                if key in active_keys:
                    current -= qty
                    active_keys.remove(key)
            else:
                # start
                current += qty
                active_keys.add(key)

                if current > peak_existing:
                    peak_existing = current
                    peak_active_keys = set(active_keys)

        existing = peak_existing

        if existing + quantity > stock:
            # Collect conflicting booking IDs and dates 
            # For conflicts, expose the effective end (cooling_period_end if present else return_date)
            conflict_rows = [
                booking_lookup[key]
                for key in peak_active_keys
                if key in booking_lookup
            ]

            # Fallback: if peak set is empty for any reason, surface all overlaps.
            if not conflict_rows:
                conflict_rows = list(existing_bookings)

            conflicts = [
                {
                    "booking_id": b.booking.id,
                    "shop_booking_id": b.booking.shop_booking_id,
                    "pickup_date": format_datetime(b.booking.pickup_date),
                    "return_date": format_datetime(b.booking.return_date if b.booking.return_date else None),
                    "cooling_period_end": format_datetime(b.booking.cooling_period_end) if b.booking.cooling_period_end else None,
                }
                for b in conflict_rows
            ]

            logger.error(
                f"\nInsufficient stock for Variant ID {variant_id} ({product_name}, {attribute}).\n "
                f"Requested: {quantity}, Existing: {existing}, Stock: {stock}.\n "   
                f"Conflicts: {conflicts}" 
            )

            # Prepare response data with booking details from first conflict
            response_data = {
                "variant_id": variant_id,
                "product_name": product_name,
                "attribute": attribute,
                "requested_quantity": quantity,
                "existing_booked_quantity": existing,
                "available_stock": stock,
                "out_of_stock_between": {
                    "from": format_datetime(pickup_datetime),
                    "to": format_datetime(return_datetime),
                },
            }
            
            # Add booking details from the first conflicting booking
            if conflicts:
                response_data["booking_id"] = conflicts[0]["booking_id"]
                response_data["shop_booking_id"] = conflicts[0]["shop_booking_id"]
            
            return Response(
                {
                    "status": "insufficient_stock",
                    "message": "Insufficient stock for the requested product variant.",
                    "data": response_data,
                },            
                status=status.HTTP_400_BAD_REQUEST,
            )

    return None

def add_product_variant_availability(variants, pickup_date_obj, return_date_obj):

    for variant_info in variants:
        variant_id = variant_info["id"]
        booked_quantity = variant_info["quantity"]
        current_date = pickup_date_obj

        while current_date <= return_date_obj:
            availability, created = ProductVariantAvailability.objects.get_or_create(
                variant_id=variant_id,
                date=current_date,
                defaults={"booked_quantity": booked_quantity},
            )
            if created:
                logs.info(f"Created availability: Variant {variant_id}, Date {current_date}, Quantity {booked_quantity}")
            else:
                availability.booked_quantity += booked_quantity
                availability.save()
            current_date += timedelta(days=1)



def remove_product_variant_availability(variants, start_date, end_date):
    for variant_info in variants:
        variant_id = variant_info["id"]
        variant_quantity = variant_info["quantity"]
        current_date = start_date

        while current_date <= end_date:
            availability = ProductVariantAvailability.objects.filter(
                variant_id=variant_id, date=current_date
            ).first()
            if availability:
                before = availability.booked_quantity
                availability.booked_quantity = max(0, availability.booked_quantity - variant_quantity)
                if availability.booked_quantity == 0:
                    availability.delete()
                else:
                    availability.save()
            else:
                print(f"⚠️ No availability found to remove for Variant {variant_id} on {current_date}")
            current_date += timedelta(days=1)
