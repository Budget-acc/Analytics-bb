import logging

from django.db import transaction

from booking_v2.models import BookingV2
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Delete booking",
    description="Soft deletes the booking and performs best-effort ledger cleanup.",
    parameters=[
        OpenApiParameter(
            name="pk",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description="Booking ID.",
        )
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Booking deleted successfully."),
        404: OpenApiResponse(description="Exact message: Booking not found."),
        500: OpenApiResponse(description="Exact message: Unable to delete booking."),
    },
)
def destroy(self, request, pk=None):
    """Soft-delete a booking. Ledger cleanup is best-effort and never blocks deletion."""
    try:
        booking = (
            BookingV2.objects
            .filter(shop_id=self.shop_id, id=pk)
            .select_related("shop")
            .first()
        )
        if not booking:
            return self.not_found_response(
                resource="Booking",
                message="Booking not found.",
            )

        # Collect all ledger-relevant IDs before deletion while relations are accessible.
        payment_ids = list(
            booking.payments.filter(account__isnull=False).values_list("id", flat=True)
        )
        security_ids = list(
            booking.security_deposit_payments.filter(account__isnull=False).values_list("id", flat=True)
        )
        refund_ids = list(
            booking.refunds.filter(account__isnull=False).values_list("id", flat=True)
        )
        adjustment_ids = list(
            booking.security_adjustments.filter(
                account__isnull=False, adjustment_type="refund"
            ).values_list("id", flat=True)
        )

        booking_shop = booking.shop
        booking_shop_booking_id = booking.shop_booking_id

        # Primary action — soft-delete the booking. Must always succeed.
        with transaction.atomic():
            booking.delete()

        # Best-effort ledger cleanup — failures are logged, never raised.
        try:
            from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
            if payment_ids:
                _delete_ledger_entries(reference_ids=payment_ids, reference_type="PaymentDetailsV2")
            if security_ids:
                _delete_ledger_entries(reference_ids=security_ids, reference_type="SecurityDepositPayment")
            if refund_ids:
                _delete_ledger_entries(reference_ids=refund_ids, reference_type="RefundDetails")
            if adjustment_ids:
                _delete_ledger_entries(reference_ids=adjustment_ids, reference_type="SecurityDepositAdjustment")
        except Exception as ledger_err:
            logger.warning(
                f"Ledger cleanup failed for booking {pk} — orphaned entries may exist. Error: {ledger_err}"
            )

        try:
            create_activity_log(
                shop=booking_shop,
                user=request.user,
                instance=booking,
                action="deleted",
                message=f"Booking #{booking_shop_booking_id} deleted.",
            )
        except Exception as log_err:
            logger.warning(f"Failed to create activity log for booking delete: {log_err}")

        return self.success_response(message="Booking deleted successfully.")

    except Exception as e:
        logger.error(f"Error deleting booking {pk}: {e}", exc_info=True)
        return self.error_response("Unable to delete booking.")


__all__ = ["destroy"]
