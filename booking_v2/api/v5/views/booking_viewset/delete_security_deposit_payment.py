import logging
from django.db import transaction
from rest_framework.decorators import action
from booking_v2.models import BookingV2, SecurityDepositPayment, SecurityDepositAdjustment
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Delete security deposit payment",
    description="Deletes security deposit payment if booking is not completed and has no adjustments.",
    parameters=[OpenApiParameter(name="payment_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Security deposit payment ID.")],
    responses={
        200: OpenApiResponse(description="Exact message: Security deposit payment deleted successfully."),
        400: OpenApiResponse(description="Exact messages include: Cannot be deleted from completed bookings.; Security deposit has associated adjustments and cannot be deleted."),
        404: OpenApiResponse(description="Exact messages include: Security Deposit Payment not found.; Booking not found."),
        500: OpenApiResponse(description="Exact message: Failed to delete security deposit payment."),
    },
)
@action(detail=False, methods=["delete"], url_path="delete-security-deposit-payment/(?P<payment_id>[^/.]+)")
@transaction.atomic
def delete_security_deposit_payment(self, request, *args, **kwargs):

    payment_id = kwargs.get("payment_id")
    savepoint = transaction.savepoint()

    try:
        # Step 1: Fetch without lock for validation
        try:
            payment = SecurityDepositPayment.objects.select_related("booking").get(
                id=payment_id,
                booking__shop_id=self.shop_id,
            )
        except SecurityDepositPayment.DoesNotExist:
            return self.not_found_response(resource="Security Deposit Payment")

        booking_id = payment.booking_id
        deleted_amount = payment.amount

        # Step 2: Lock booking
        try:
            locked_booking = BookingV2.objects.select_for_update().get(id=booking_id)
        except BookingV2.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.not_found_response(resource="Booking")

        # Step 3: Block deletion for completed bookings
        if locked_booking.booking_status == "completed":
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"booking_status": ["Cannot delete security deposit from a completed booking."]},
                message="Cannot be deleted from completed bookings."
            )

        # Step 5: Block if any adjustments exist for this booking
        if SecurityDepositAdjustment.objects.filter(booking_id=booking_id).exists():
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security": ["Cannot delete security deposit — adjustments exist for this booking."]},
                message="Security deposit has associated adjustments and cannot be deleted."
            )

        # Step 6: Reuse existing helper — deletes SDP record, ledger entries, and cascades daily balance
        error_response = self._update_security_deposit_payment(
            locked_booking,
            new_security_amount=0,
            savepoint=savepoint,
        )
        if error_response:
            return error_response

        # Step 7: Zero out booking.security_amount and persist
        locked_booking.security_amount = 0
        locked_booking.save(update_fields=['security_amount'])

        transaction.savepoint_commit(savepoint)

        try:
            create_activity_log(
                shop=locked_booking.shop,
                user=request.user,
                instance=locked_booking,
                action="deleted",
                message=f"Security deposit payment of ₹{deleted_amount:.2f} deleted."
            )
        except Exception as log_error:
            logger.exception(
                f"Failed to create activity log for security deposit deletion for booking {booking_id}: {log_error}"
            )

        return self.success_response(
            data={
                "deleted_amount": float(deleted_amount),
                "updated_security_amount": 0,
            },
            message="Security deposit payment deleted successfully."
        )

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.exception(f"Security deposit payment deletion failed for payment {payment_id}")
        return self.error_response(message="Failed to delete security deposit payment.", dev_message=str(e))


__all__ = ["delete_security_deposit_payment"]
