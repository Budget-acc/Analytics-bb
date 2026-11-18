from django.db import transaction
from booking_v2.models import BookingV2, PaymentDetailsV2, SecurityDepositPayment, RefundDetails, SecurityDepositAdjustment
from booking_v2.api.v4.utils.availability import remove_product_variant_availability
from drf_spectacular.utils import OpenApiResponse, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V4"],
    summary="Delete booking",
    description=(
        "Soft-deletes a booking, rolls back product variant availability (respecting cooling period), "
        "and removes associated ledger entries.\n\n"
        "Purpose: Safely remove invalid or cancelled bookings while preserving inventory consistency.\n\n"
        "Context: Use when a booking must be fully cancelled and cleaned up."
    ),
    responses={
        200: OpenApiResponse(
            description="Success.\n\nExact message:\n- Booking deleted successfully."
        ),
        404: OpenApiResponse(
            description="Not found.\n\nExact message:\n- Booking not found."
        ),
        500: OpenApiResponse(
            description="Server error.\n\nExact message:\n- An error occurred while deleting the booking. Please try again."
        ),
    },
)
@transaction.atomic
def destroy(self, request, pk=None):
    try:
        booking = BookingV2.objects.get(id=pk)

        variants = [
            {
                "id": item.product_variant_id,
                "quantity": item.quantity
            }
            for item in booking.booked_items.all()
            if item.product_variant_id
        ]

        start_date = booking.pickup_date
        end_date = booking.cooling_period_end if booking.cooling_period_end else booking.return_date

        # Remove product availability
        if variants:
            remove_product_variant_availability(variants, start_date, end_date)

        # Delete ledger entries for all account-linked records before soft-deleting
        try:
            from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries

            payment_ids = list(PaymentDetailsV2.objects.filter(
                booking=booking, account__isnull=False
            ).values_list('id', flat=True))
            if payment_ids:
                _delete_ledger_entries(reference_ids=payment_ids, reference_type='PaymentDetailsV2')

            security_ids = list(SecurityDepositPayment.objects.filter(
                booking=booking, account__isnull=False
            ).values_list('id', flat=True))
            if security_ids:
                _delete_ledger_entries(reference_ids=security_ids, reference_type='SecurityDepositPayment')

            refund_ids = list(RefundDetails.objects.filter(
                booking=booking, account__isnull=False
            ).values_list('id', flat=True))
            if refund_ids:
                _delete_ledger_entries(reference_ids=refund_ids, reference_type='RefundDetails')

            adjustment_ids = list(SecurityDepositAdjustment.objects.filter(
                booking=booking, adjustment_type='refund', account__isnull=False
            ).values_list('id', flat=True))
            if adjustment_ids:
                _delete_ledger_entries(reference_ids=adjustment_ids, reference_type='SecurityDepositAdjustment')

        except Exception as ledger_err:
            logger.exception(f"Failed to delete ledger entries for booking {pk}: {ledger_err}")

        # Soft delete
        booking.delete()
        return self.success_response(
            data={},
            message="Booking deleted successfully.",
        )

    except BookingV2.DoesNotExist:
        return self.not_found_response(
            resource="Booking",
            message="Booking not found.",
            dev_message=f"Booking with id {pk} not found."
        )
    except Exception as e:
        logger.error(f"Booking delete failed for booking ID {pk}: {str(e)}", exc_info=True)
        return self.error_response(
            message="An error occurred while deleting the booking. Please try again.",
            dev_message=str(e)
        )
