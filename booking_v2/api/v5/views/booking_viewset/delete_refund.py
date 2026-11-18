import logging
from decimal import Decimal
from django.db import transaction
from rest_framework.decorators import action
from booking_v2.models import BookingV2, RefundDetails, SecurityDepositPayment
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Delete booking refund",
    description="Deletes a refund record for a booking when allowed by business rules.",
    parameters=[
        OpenApiParameter(name="booking_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID."),
        OpenApiParameter(name="refund_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Refund ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Refund deleted successfully."),
        400: OpenApiResponse(description="Exact messages include: Invalid identifiers provided.; Cannot delete refund for a completed booking.; Cannot delete refund: total paid would exceed total payable."),
        404: OpenApiResponse(description="Exact messages include: Refund not found for this booking.; Refund not found.; Booking not found."),
        500: OpenApiResponse(description="Exact message: Failed to delete refund. Please try again later."),
    },
)
@action(detail=False, methods=["delete"], url_path=r"refund/(?P<booking_id>[^/.]+)/(?P<refund_id>[^/.]+)")
@transaction.atomic
def delete_refund(self, request, *args, **kwargs):
    """
    Delete a refund record.

    URL: .../refund/{booking_id}/{refund_id}/  (DELETE)

    Validations:
     - refund must exist and belong to booking
     - booking must belong to current shop
     - disallow if booking is completed or items returned
    Behavior:
     - delete refund (DB)
     - recompute booking.advance_amount as payments_total - refunds_total (remaining refunds after deletion)
     - update booking.payment_status and clear payment_method if no payments remain
     - return user-friendly message and developer dev_message
    """
    booking_id = kwargs.get("booking_id")
    refund_id = kwargs.get("refund_id")

    # Validate ids
    try:
        booking_id = int(booking_id)
        refund_id = int(refund_id)
    except Exception:
        return self.validation_error_response(
            errors={"ids": ["booking_id and refund_id must be integers."]},
            message="Invalid identifiers provided."
        )

    try:
        # quick existence check (no lock) and ensure refund belongs to booking and shop
        refund_locked = (
            RefundDetails.objects
            .select_related("booking")
            .select_for_update()
            .filter(id=refund_id, booking_id=booking_id, booking__shop_id=self.shop_id)
            .first()
        )
        if not refund_locked:
            return self.not_found_response(
                resource="Refund",
                message="Refund not found for this booking."
            )

        booking_locked = refund_locked.booking
        # business constraints: don't allow deleting refund for completed/returned bookings
        booking_locked = (
            BookingV2.objects
            .select_for_update()
            .filter(id=booking_locked.id, shop_id=self.shop_id)
            .first()
        )

        if booking_locked.booking_status == "completed":
            return self.validation_error_response(
                errors={"booking": ["Cannot modify refunds for a completed booking."]},
                message="Cannot delete refund for a completed booking.",
                dev_message=f"Booking {booking_id} status=completed"
            )

        removed_amount = Decimal(refund_locked.refunded_amount or 0)

        # Total Paid must never exceed Total Payable. Simulate what paid
        total_payable = Decimal(str(booking_locked.total_amount or 0)) - Decimal(str(booking_locked.discount_amount or 0))
        simulated_paid = (
            Decimal(str(booking_locked.advance_amount or 0)) + removed_amount
        ) 

        if simulated_paid > total_payable:
            return self.validation_error_response(
                errors={"refund": ["Deleting this refund would make the total paid amount exceed the total payable amount."]},
                message="Cannot delete refund: total paid would exceed total payable.",
                dev_message=(
                    f"Booking {booking_id}: simulated_paid={simulated_paid} "
                    f"> total_payable={total_payable}"
                )
            )

        # Delete ledger entry (new flow only)
        if refund_locked.account_id:
            from finance.api.v3.utils import (
                delete_ledger_entries as _delete_ledger_entries,
                update_daily_balance as _update_daily_balance,
            )
            _delete_ledger_entries(
                reference_ids=[refund_locked.id],
                reference_type='RefundDetails',
            )
            _update_daily_balance(
                shop=booking_locked.shop_id,
                account=refund_locked.account_id,
                date=refund_locked.created_at.date(),
            )

        # Delete refund
        refund_locked.delete()

        # Reverse the effect of the refund: add back the refunded amount to advance_amount
        booking_locked.advance_amount = Decimal(str(booking_locked.advance_amount or 0)) + Decimal(str(removed_amount))

        # Recalculate payment_status against the restored advance amount
        # (total_payable = total_amount - discount_amount, already computed above)
        booking_locked.payment_status = booking_locked.advance_amount >= total_payable

        booking_locked.save(
            update_fields=[
                'advance_amount',
                'payment_status',
                'payment_method'
            ] if getattr(booking_locked, 'payment_method', None) is not None
            else [
                'advance_amount',
                'payment_status'
            ]
        )

        try:
            create_activity_log(
                shop=booking_locked.shop,
                user=request.user,
                instance=booking_locked,
                action="deleted",
                message=f"Refund of \u20b9{removed_amount} deleted."
            )
        except Exception:
            logger.exception("Failed to create activity log for refund")

        return self.success_response(
            data={
                "refund_id": refund_id,
                "booking_id": booking_locked.id,
                "refunded_amount_removed": float(removed_amount),
                "updated_advance_amount": booking_locked.advance_amount,
                "payment_status": booking_locked.payment_status
            },
            message="Refund deleted successfully.",
            dev_message=f"Deleted refund {refund_id} for booking {booking_id}; removed={removed_amount}"
        )

    except RefundDetails.DoesNotExist:
        return self.not_found_response(
            resource="Refund",
            message="Refund not found."
        )
    except BookingV2.DoesNotExist:
        return self.not_found_response(
            resource="Booking",
            message="Booking not found."
        )
    except Exception as e:
        logger.exception(f"Failed deleting refund {refund_id} for booking {booking_id}: {e}")
        return self.error_response(
            message="Failed to delete refund. Please try again later.",
            dev_message=str(e)
        )


__all__ = ["delete_refund"]
