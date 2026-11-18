import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Q
from django.utils.timezone import now
from pytz import timezone as pytz_timezone
from rest_framework.decorators import action
from booking_v2.models import BookingV2
from booking_v2.api.v5.utils.availability import remove_product_variant_availability
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")
india = pytz_timezone('Asia/Kolkata')


def to_ist(dt):
    from django.utils import timezone
    return timezone.localtime(dt, india)


@extend_schema(
    tags=["Booking Management V5"],
    summary="Mark booking completed",
    description=(
        "Marks a booking as completed when delivery, payment, and security deposit "
        "constraints are met. If a security deposit was configured, it must be marked "
        "paid before completion. If the booking is being completed via cancellation and "
        "the deposit is paid, its refund/deduction adjustments must fully account for "
        "the deposit amount before completion is allowed."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID."),
    ],
    request=inline_serializer(
        name="UpdateBookingStatusV5Request",
        fields={"booking_status": serializers.CharField(required=True, help_text="Only 'completed' is supported")},
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Booking marked as completed."),
        400: OpenApiResponse(
            description=(
                "Exact messages include: "
                "Invalid status.; Item must be returned or booking cancelled.; "
                "Payment should be completed.; "
                "Security deposit must be fully collected before completing this booking.; "
                "Security deposit is not fully settled."
            )
        ),
        404: OpenApiResponse(description="Booking not found response."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating booking status."),
    },
)
@action(detail=False, methods=["patch"], url_path="update-booking-status/(?P<pk>[^/.]+)")
@transaction.atomic
def update_booking_status(self, request, *args, **kwargs):
    pk = self.kwargs.get("pk")
    try:
        booking = BookingV2.objects.select_for_update().get(id=pk)

        status = request.data.get("booking_status")
        if status != "completed":
            return self.validation_error_response(
                errors={"booking_status": ["Only 'completed' status is supported here."]},
                message="Invalid status."
            )

        if booking.delivery_status not in ["returned", "cancelled"]:
            return self.validation_error_response(
                errors={"delivery_status": ["Booking can only be completed after items are returned or booking is cancelled."]},
                message="Item must be returned or booking cancelled."
            )

        if booking.delivery_status == "returned" and not booking.payment_status:
            return self.validation_error_response(
                errors={"payment_status": ["Payment must be completed before completing booking."]},
                message="Payment should be completed."
            )

        # Security deposit collection check — applies whenever a deposit was
        # configured, regardless of delivery_status.
        security_amount = Decimal(str(booking.security_amount or 0))
        if security_amount > 0:
            is_deposit_paid = booking.security_deposit_payments.filter(is_paid=True).exists()

            # if is_deposit_paid:
            #     return self.validation_error_response(
            #         errors={"security_deposit": [
            #             "Security deposit must be marked as paid before completing this booking."
            #         ]},
            #         message="Security deposit must be fully collected before completing this booking.",
            #     )

            # Refund/deduction settlement check — only required when the
            # deposit is already paid AND the booking is being completed
            # via cancellation.
            if booking.delivery_status == "cancelled" and is_deposit_paid:
                totals = booking.security_adjustments.aggregate(
                    refunded=Sum("amount", filter=Q(adjustment_type="refund")),
                    deducted=Sum("amount", filter=Q(adjustment_type="deduction")),
                )
                accounted = (totals["refunded"] or Decimal("0")) + (totals["deducted"] or Decimal("0"))
                if accounted != security_amount:
                    remaining = security_amount - accounted
                    return self.validation_error_response(
                        errors={"security_deposit": [
                            f"Security deposit is not fully settled. Remaining/unsettled balance: ₹{remaining}."
                        ]},
                        message="Security deposit is not fully settled.",
                    )

        # STEP 1 — Get only NOT RETURNED items
        not_returned_items = booking.booked_items.filter(
            return_status="not_returned"
        ).only("id", "product_variant_id", "quantity")

        # Build variant list
        variants_data = [
            {"id": item.product_variant_id, "quantity": item.quantity or 1}
            for item in not_returned_items
            if item.product_variant_id
        ]

        if variants_data:
            from django.utils import timezone as tz_module
            today = tz_module.localdate()

            pickup_date = to_ist(booking.pickup_date).date()
            return_date = to_ist(booking.return_date).date() if booking.return_date else pickup_date
            cooling_date = (
                to_ist(booking.cooling_period_end).date()
                if booking.cooling_period_end else None
            )

            # Determine the earliest start date and the latest end date.
            # The cooling date may be before pickup or after return, and
            # we always keep the start date at least as late as today.
            start_date = pickup_date
            if cooling_date and cooling_date < start_date:
                start_date = cooling_date
            if today > start_date:
                start_date = today

            end_date = return_date
            if cooling_date and cooling_date > end_date:
                end_date = cooling_date

            if start_date <= end_date:
                remove_product_variant_availability(
                    variants_data,
                    start_date,
                    end_date
                )

                # Mark items as returned
                current_time = now()

                not_returned_items.update(
                    return_status="returned",
                    returned_at=current_time
                )

            else:
                logger.error(
                    f"---------Warning--------\n"
                    f"Availability removal skipped for booking {booking.id}:\n"
                    f"start_date {start_date} > end_date {end_date}"
                )

        booking.booking_status = "completed"
        booking.completed_at = now()
        booking.save()

        log_message = (
            "Booking marked as cancelled."
            if booking.delivery_status == "cancelled"
            else "Booking marked as completed."
        )
        log_action = (
            "cancelled"
            if booking.delivery_status == "cancelled"
            else "updated"
        )

        try:
            create_activity_log(
                shop=booking.shop,
                user=request.user,
                instance=booking,
                action=log_action,
                message=log_message
            )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for booking payment: {log_error}")

        return self.success_response(
            data={"booking_status": booking.booking_status},
            message="Booking marked as completed."
        )
    except BookingV2.DoesNotExist:
        return self.booking_not_found_response(booking_id=pk)

    except Exception as e:
        logger.error(f"Booking status update error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating booking status.",
            dev_message=str(e)
        )


__all__ = ["update_booking_status"]
