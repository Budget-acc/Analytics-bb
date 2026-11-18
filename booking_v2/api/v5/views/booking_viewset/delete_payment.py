import logging
from decimal import Decimal
from django.db import models, transaction
from rest_framework.decorators import action
from safedelete.models import HARD_DELETE
from booking_v2.models import BookingV2, PaymentDetailsV2, RefundDetails
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Delete booking payment",
    description="Deletes a payment entry and recalculates booking advance and payment status.",
    parameters=[
        OpenApiParameter(name="payment_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Payment record ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Payment deleted successfully."),
        400: OpenApiResponse(description="Exact messages include: Payment cannot be deleted from completed bookings.; Cannot delete payment - amount exceeds total paid."),
        404: OpenApiResponse(description="Exact message: Payment record not found."),
        500: OpenApiResponse(description="Exact messages include: Failed to delete payment record.; Something went wrong while deleting the payment."),
    },
)
@action(detail=False, methods=["delete"], url_path="delete-payment/(?P<payment_id>[^/.]+)")
@transaction.atomic
def delete_payment(self, request, *args, **kwargs):
    """
    Delete a payment record and update booking payment status.
    Validates payment belongs to shop and handles all edge cases.
    """
    payment_id = kwargs.get("payment_id")

    try:
        # -------- Step 1: Fetch Payment Without Locking (for validation) --------
        try:
            payment = PaymentDetailsV2.objects.select_related("booking").get(
                id=payment_id,
                booking__shop_id=self.shop_id,
                booking__deleted__isnull=True
            )
        except PaymentDetailsV2.DoesNotExist:
            return self.not_found_response(
                resource="Payment",
                message="Payment record not found.",
                dev_message=f"Payment with ID {payment_id} not found for shop {self.shop_id} or payment belongs to deleted booking."
            )

        booking_id = payment.booking_id
        payment_amount = payment.payment_amount

        # -------- Step 2: Lock Payment and Booking Separately --------
        try:
            # Lock the payment record
            locked_payment = PaymentDetailsV2.objects.select_for_update().get(id=payment_id)

            # Lock the booking record
            locked_booking = BookingV2.objects.select_for_update().get(id=booking_id)

        except (PaymentDetailsV2.DoesNotExist, BookingV2.DoesNotExist):
            return self.not_found_response(
                resource="Payment",
                message="Payment record not found.",
                dev_message=f"Payment {payment_id} or booking {booking_id} not found during locking."
            )

        # -------- Business Logic Validations --------

        # 1. Cannot delete payment from completed booking
        if locked_booking.booking_status == "completed":
            return self.validation_error_response(
                errors={"booking_status": ["Cannot delete payment from completed booking."]},
                message="Payment cannot be deleted from completed bookings.",
                dev_message=f"Booking {locked_booking.id} is completed, payment deletion not allowed."
            )

        # 3. Reconcile remaining payments against refunds to preserve net paid amount
        payment_stats = PaymentDetailsV2.objects.filter(
            booking_id=booking_id
        ).aggregate(
            total_count=models.Count('id'),
            other_payments_total=models.Sum('payment_amount', filter=~models.Q(id=payment_id))
        )

        refund_stats = RefundDetails.objects.filter(
            booking_id=booking_id
        ).aggregate(
            total_refunds=models.Sum('refunded_amount')
        )

        total_payments_count = payment_stats['total_count'] or 0
        other_payments_total = payment_stats['other_payments_total'] or 0
        total_refunds = Decimal(str(refund_stats['total_refunds'] or 0))

        # -------- Calculate New Payment State --------
        current_advance = locked_booking.advance_amount or 0
        new_advance = current_advance - payment_amount
        recalculated_new_advance = other_payments_total - total_refunds

        # Ensure new advance doesn't go negative
        if new_advance < 0:
            return self.validation_error_response(
                errors={"payment_amount": ["Payment amount exceeds total paid amount."]},
                message="Cannot delete payment - amount exceeds total paid.",
                dev_message=f"Deleting payment {payment_id} would result in negative advance amount. Current: {current_advance}, Payment: {payment_amount}."
            )

        if recalculated_new_advance < 0:
            return self.validation_error_response(
                errors={"payment_amount": ["Delete related refund entries before deleting this payment."]},
                message="Cannot delete payment - amount exceeds total paid.",
                dev_message=(
                    f"Deleting payment {payment_id} would make net paid negative. "
                    f"remaining_payments={other_payments_total}, total_refunds={total_refunds}, "
                    f"recalculated_new_advance={recalculated_new_advance}"
                )
            )

        # Verify our calculation matches the persisted payment/refund history.
        if recalculated_new_advance != new_advance:
            logger.warning(
                "Payment calculation mismatch for booking %s. Expected by current advance: %s, "
                "recalculated from history: %s (remaining_payments=%s, refunds=%s)",
                locked_booking.id,
                new_advance,
                recalculated_new_advance,
                other_payments_total,
                total_refunds,
            )
            new_advance = recalculated_new_advance

        # -------- Calculate Payment Status --------
        total_amount = locked_booking.total_amount or 0
        discount_amount = locked_booking.discount_amount or 0
        final_payable = total_amount - discount_amount

        if final_payable <= 0:
            new_payment_status = True  # Always paid if nothing to pay
        else:
            new_payment_status = new_advance >= final_payable

        # -------- Create Savepoint for Nested Transaction --------
        savepoint = transaction.savepoint()

        try:
            # -------- Delete Ledger Entry (new flow only) --------
            if locked_payment.account_id:
                from finance.api.v3.utils import (
                    delete_ledger_entries as _delete_ledger_entries,
                    update_daily_balance as _update_daily_balance,
                )
                _delete_ledger_entries(
                    reference_ids=[locked_payment.id],
                    reference_type='PaymentDetailsV2',
                )
                _update_daily_balance(
                    shop=locked_booking.shop_id,
                    account=locked_payment.account_id,
                    date=locked_payment.created_at.date(),
                )

            # -------- Delete Payment Record --------
            locked_payment.delete(force_policy=HARD_DELETE)
            logger.info(f"Payment {payment_id} deleted successfully for booking {locked_booking.id}")

            # -------- Update Booking Payment Information --------
            locked_booking.advance_amount = new_advance
            locked_booking.payment_status = new_payment_status

            # Clear payment method if no payments remain
            if total_payments_count == 1:  # This was the only payment
                locked_booking.payment_method = None

            locked_booking.save(update_fields=['advance_amount', 'payment_status'])

            # -------- Commit Transaction --------
            transaction.savepoint_commit(savepoint)

            # -------- Build Success Response --------
            remaining_balance = max(final_payable - new_advance, 0) if final_payable > 0 else 0

            try:
                create_activity_log(
                    shop=locked_booking.shop,
                    user=request.user,
                    instance=locked_booking,
                    action="payment_deleted",
                    message=f"Deleted payment of \u20b9{payment_amount}."
                )
            except Exception:
                logger.exception("Failed to create activity log for successful deletion")

            return self.success_response(
                data={
                    "booking_id": locked_booking.id,
                    "deleted_payment_amount": payment_amount,
                    "updated_advance_amount": new_advance,
                    "payment_status": new_payment_status,
                    "remaining_balance": remaining_balance,
                    "total_payments_remaining": total_payments_count - 1,
                    "final_payable_amount": final_payable
                },
                message="Payment deleted successfully."
            )

        except Exception as e:
            # -------- Rollback on Failure --------
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Failed to delete payment {payment_id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to delete payment record.",
                dev_message=f"Database error while deleting payment {payment_id}: {str(e)}"
            )

    except Exception as e:
        logger.error(f"Payment deletion error for payment {payment_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the payment.",
            dev_message=f"Unexpected error in delete_payment for payment {payment_id}: {str(e)}"
        )


__all__ = ["delete_payment"]
