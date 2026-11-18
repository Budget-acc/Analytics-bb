import logging
from datetime import datetime, timedelta, time
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.timezone import make_aware, now
from django.utils import timezone
from pytz import timezone as pytz_timezone
from rest_framework.decorators import action
from booking_v2.models import BookingV2, BookedProductVariant
from booking_v2.api.v5.utils.availability import (
    check_product_variant_availability,
    add_product_variant_availability,
    remove_product_variant_availability,
)
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")
india = pytz_timezone('Asia/Kolkata')


def to_ist(dt):
    return timezone.localtime(dt, india)


@extend_schema(
    tags=["Booking Management V5"],
    summary="Process partial return",
    description="Marks selected booking items as returned/not-returned and optionally extends expected return date.",
    parameters=[OpenApiParameter(name="booking_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID.")],
    request=inline_serializer(
        name="PartialReturnV5Request",
        fields={
            "returned_items": serializers.ListField(child=serializers.IntegerField(), required=False),
            "not_returned_items": serializers.ListField(child=serializers.IntegerField(), required=False),
            "expected_return_date": serializers.CharField(required=False, help_text="DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS"),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Partial return processed successfully."),
        400: OpenApiResponse(description="Exact messages include: Invalid booking id.; Invalid request format.; Items must be lists.; All item IDs must be integers.; Invalid item classification.; No items provided.; Cannot return before pickup date.; Product not found.; Invalid date format.; Cannot reduce return date.; Booking has no active items."),
        500: OpenApiResponse(description="Server/business failure response."),
    },
)
@action(detail=False, methods=["post"], url_path="partial-return/(?P<booking_id>[^/.]+)")
@transaction.atomic
def partial_return(self, request, *args, **kwargs):

    booking_id_raw = kwargs.get("booking_id")

    try:
        booking_id = int(booking_id_raw)
    except (TypeError, ValueError):
        return self.validation_error_response(
            errors={"booking_id": ["Invalid booking id. Must be integer."]},
            message="Invalid booking id."
        )

    if not isinstance(request.data, dict):
        return self.validation_error_response(
            errors={"request": ["Invalid request format."]},
            message="Invalid request format."
        )

    returned_items = request.data.get("returned_items", [])
    not_returned_items = request.data.get("not_returned_items", [])
    expected_return_date = request.data.get("expected_return_date")

    if not isinstance(returned_items, list) or not isinstance(not_returned_items, list):
        return self.validation_error_response(
            errors={"items": ["Items must be lists."]},
            message="Items must be lists."
        )

    try:
        returned_ids = {int(i) for i in returned_items}
        not_returned_ids = {int(i) for i in not_returned_items}
    except (TypeError, ValueError):
        return self.validation_error_response(
            errors={"ids": ["All item IDs must be integers."]},
            message="All item IDs must be integers."
        )

    if returned_ids & not_returned_ids:
        return self.validation_error_response(
            errors={"items": ["Same item cannot be both returned and not_returned."]},
            message="Invalid item classification."
        )

    all_ids = returned_ids | not_returned_ids

    if not all_ids:
        return self.validation_error_response(
            errors={"items": ["No items provided."]},
            message="No items provided."
        )

    # LOCK BOOKING ROW
    booking = get_object_or_404(
        BookingV2.objects.select_for_update(of=("self",)),
        id=booking_id,
        shop_id=self.shop_id,
        deleted__isnull=True
    )

    pickup_date = to_ist(booking.pickup_date).date()
    base_booking_return_date = to_ist(
        booking.original_return_date or booking.return_date
    ).date()

    today_ist = to_ist(timezone.now()).date()

    if today_ist < pickup_date:
        return self.validation_error_response(
            errors={
                "return": [
                    f"Return not allowed before pickup date ({pickup_date})."
                ]
            },
            message="Cannot return before pickup date."
        )

    # LOCK ONLY BOOKED VARIANTS
    booked_variants = list(
        BookedProductVariant.objects
        .select_for_update()
        .filter(id__in=all_ids, booking_id=booking.id)
    )

    if len(booked_variants) != len(all_ids):
        return self.validation_error_response(
            errors={"booked_variant_ids": ["Invalid booked variant IDs."]},
            message="Product not found."
        )

    booked_map = {bv.id: bv for bv in booked_variants}
    current_time = now()

    # PROCESS RETURNED ITEMS
    for variant_id in returned_ids:
        bv = booked_map[variant_id]

        if bv.return_status == "returned":
            return self.error_response(
                message=f"{bv.product_name} already returned.",
                dev_message=f"Variant {bv.id} already returned."
            )

        # Determine actual active end per variant
        variant_active_end = (
            to_ist(bv.extended_until).date()
            if bv.extended_until
            else base_booking_return_date
        )

        remove_product_variant_availability(
            [{
                "id": bv.product_variant.id,
                "quantity": bv.quantity
            }],
            pickup_date,
            variant_active_end
        )

        bv.return_status = "returned"
        bv.returned_at = current_time
        bv.extended_until = None

    # PROCESS NOT RETURNED (EXTENSIONS)
    new_return_datetime = None

    if expected_return_date:
        parsed_date = None

        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                parsed_date = datetime.strptime(expected_return_date, fmt).date()
                break
            except ValueError:
                continue

        if not parsed_date:
            return self.validation_error_response(
                errors={"expected_return_date": ["Invalid date format (Use YYYY-MM-DD or DD-MM-YYYY)."]},
                message="Invalid date format."
            )

        new_return_datetime = make_aware(
            datetime.combine(parsed_date, time(23, 59, 59))
        )

    for variant_id in not_returned_ids:
        bv = booked_map[variant_id]

        if bv.return_status == "returned":
            return self.error_response(
                message=f"Variant {bv.id} already returned.",
                dev_message=f"Variant {bv.id} already returned."
            )

        bv.return_status = "not_returned"

        if new_return_datetime:
            # Current active end for this variant
            variant_current_end = (
                to_ist(bv.extended_until).date()
                if bv.extended_until
                else base_booking_return_date
            )

            new_return_date = new_return_datetime.date()

            # Cannot reduce date
            if new_return_date < variant_current_end:
                return self.validation_error_response(
                    errors={"expected_return_date": ["Cannot reduce return date."]},
                    message="Cannot reduce return date."
                )

            # If equal - skip safely (no duplicate availability)
            if new_return_date == variant_current_end:
                continue

            extension_start = variant_current_end + timedelta(days=1)
            extension_end = new_return_date

            conflict = check_product_variant_availability(
                [{
                    "id": bv.product_variant.id,
                    "quantity": bv.quantity
                }],
                make_aware(datetime.combine(extension_start, datetime.min.time())),
                new_return_datetime,
                exclude_booking_id=booking.id
            )

            if conflict:
                return conflict

            add_product_variant_availability(
                [{
                    "id": bv.product_variant.id,
                    "quantity": bv.quantity
                }],
                extension_start,
                extension_end
            )

            bv.extended_until = new_return_datetime

    # BULK UPDATE VARIANTS
    BookedProductVariant.objects.bulk_update(
        booked_variants,
        ["return_status", "returned_at", "extended_until"]
    )

    # RECALCULATE BOOKING RETURN DATE
    if expected_return_date:
        active_end_dates = []

        variants = BookedProductVariant.objects.filter(
            booking_id=booking.id,
            return_status="not_returned",
            deleted__isnull=True
        )

        for v in variants:
            active_end = v.extended_until or booking.original_return_date or booking.return_date
            if active_end:
                active_end_dates.append(active_end)

        if active_end_dates:
            booking.return_date = max(active_end_dates)

        booking.save(update_fields=["return_date"])

    # ---- DELIVERY STATUS AUTO UPDATE ----
    all_items = (
        BookedProductVariant.objects
        .select_for_update()
        .filter(booking_id=booking.id, deleted__isnull=True)
    )

    total_items = len(all_items)

    # Safety guard
    if total_items == 0:
        return self.error_response(
            message="Booking has no active items.",
            dev_message=f"Booking {booking.id} has zero active booked variants."
        )

    returned_count = sum(1 for item in all_items if item.return_status == "returned")

    all_returned = (returned_count == total_items)

    # Only update delivery_status
    if (
        all_returned
        and booking.delivery_status != "returned"
        and booking.delivery_status != "cancelled"
    ):
        booking.delivery_status = "returned"
        booking.save(update_fields=["delivery_status"])

    return self.success_response(
        data={
            "booking_id": booking.id,
            "returned_count": len(returned_ids),
            "not_returned_count": len(not_returned_ids),
        },
        message="Partial return processed successfully."
    )


__all__ = ["partial_return"]
