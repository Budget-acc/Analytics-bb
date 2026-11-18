from django.db import transaction
from booking_v2.models import BookingV2, PaymentDetailsV2, SecurityDepositPayment, RefundDetails, SecurityDepositAdjustment
from booking_v2.api.v3.utils.availability import remove_product_variant_availability
from booking_v2.tasks import cleanup_s3_documents_task
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiResponse, extend_schema
import logging

logger = logging.getLogger("api_requests")


@transaction.atomic
@extend_schema(
    tags=["Booking Management V3"],
    summary="Delete booking",
    description=(
        "Soft deletes a booking, rolls back variant availability, and triggers document cleanup.\n\n"
        "Purpose: Safely remove bookings while preserving inventory consistency.\n\n"
        "Context: Use when invalid/cancelled booking needs full operational cleanup."
    ),
    responses={
        200: OpenApiResponse(description="Success exact message: Booking deleted successfully."),
        404: OpenApiResponse(description="Not found exact message: Booking not found."),
        500: OpenApiResponse(description="Server error exact message: An error occurred while deleting the booking. Please try again."),
    },
)
def destroy(self, request, pk=None):
    try:
        booking = (
            BookingV2.objects
            .select_related("shop")
            .prefetch_related("booked_items")
            .get(id=pk, deleted__isnull=True)
        )

        document_urls = booking.documents or []

        active_variants_qs = booking.booked_items.filter(
            return_status="not_returned",
            product_variant__isnull=False,
            deleted__isnull=True
        ).values("product_variant_id", "quantity")

        variants = [
            {
                "id": item["product_variant_id"],
                "quantity": item["quantity"],
            }
            for item in active_variants_qs
        ]

        start_date, end_date = self.get_booking_effective_window(booking)

        # Remove product availability
        if variants:
            remove_product_variant_availability(variants, start_date, end_date)

        # Collect all ledger-relevant IDs before deletion while relations are accessible.
        payment_ids = list(PaymentDetailsV2.objects.filter(
            booking=booking, account__isnull=False
        ).values_list('id', flat=True))
        security_ids = list(SecurityDepositPayment.objects.filter(
            booking=booking, account__isnull=False
        ).values_list('id', flat=True))
        refund_ids = list(RefundDetails.objects.filter(
            booking=booking, account__isnull=False
        ).values_list('id', flat=True))
        adjustment_ids = list(SecurityDepositAdjustment.objects.filter(
            booking=booking, adjustment_type='refund', account__isnull=False
        ).values_list('id', flat=True))

        # Soft delete
        booking.delete()

        # Best-effort ledger cleanup — failures are logged, never raised.
        try:
            from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
            if payment_ids:
                _delete_ledger_entries(reference_ids=payment_ids, reference_type='PaymentDetailsV2')
            if security_ids:
                _delete_ledger_entries(reference_ids=security_ids, reference_type='SecurityDepositPayment')
            if refund_ids:
                _delete_ledger_entries(reference_ids=refund_ids, reference_type='RefundDetails')
            if adjustment_ids:
                _delete_ledger_entries(reference_ids=adjustment_ids, reference_type='SecurityDepositAdjustment')
        except Exception as ledger_err:
            logger.warning(
                f"Ledger cleanup failed for booking {pk} — orphaned entries may exist. Error: {ledger_err}"
            )

        # Step 1: Try synchronous cleanup first (immediate deletion)
        sync_failed_urls = []
        if document_urls:
            sync_failed_urls = self._cleanup_booking_documents_sync(document_urls)

            if sync_failed_urls:
                # Step 2: Queue async cleanup for failed deletions
                transaction.on_commit(
                    lambda: cleanup_s3_documents_task.delay(sync_failed_urls)
                )
                logger.info(f"Queued async cleanup for {len(sync_failed_urls)} failed document deletions")
            else:
                logger.info(f"Successfully cleaned up all {len(document_urls)} documents synchronously")

        try:
            create_activity_log(
                shop=booking.shop,
                user=request.user,
                instance=booking,
                action="deleted",
                message=f"Booking #{booking.shop_booking_id} is deleted.",
            )
        except Exception:
            logger.exception("Failed to create activity log for booking delete initiation")

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
