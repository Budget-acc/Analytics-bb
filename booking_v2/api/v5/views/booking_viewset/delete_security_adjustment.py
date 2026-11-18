import logging
from django.db import transaction
from rest_framework.decorators import action
from booking_v2.models import BookingV2, SecurityDepositAdjustment
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Delete security adjustment",
    description="Deletes a security adjustment entry and related ledger impact where applicable.",
    parameters=[OpenApiParameter(name="adjustment_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Adjustment ID.")],
    responses={
        200: OpenApiResponse(description="Exact message: Adjustment deleted successfully."),
        400: OpenApiResponse(description="Exact message: Booking already completed."),
        404: OpenApiResponse(description="Adjustment not found response."),
        500: OpenApiResponse(description="Exact message: Failed to delete adjustment"),
    },
)
@action(detail=False, methods=["delete"], url_path="delete-security-adjustment/(?P<adjustment_id>[^/.]+)")
@transaction.atomic
def delete_security_adjustment(self, request, *args, **kwargs):

    adjustment_id = kwargs.get("adjustment_id")
    savepoint = transaction.savepoint()

    try:
        adjustment = SecurityDepositAdjustment.objects.select_related("booking").get(
            id=adjustment_id,
            booking__shop_id=self.shop_id,
            booking__deleted__isnull=True
        )

        booking = BookingV2.objects.select_for_update().get(id=adjustment.booking_id)

        if booking.booking_status == "completed":
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"booking": ["Cannot delete adjustment for completed booking"]},
                message="Booking already completed."
            )

        deleted_amount = adjustment.amount

        # Delete ledger entry for refund adjustments (new flow only)
        # Deduction type never has a ledger entry
        if adjustment.adjustment_type == 'refund' and adjustment.account_id:
            from finance.api.v3.utils import (
                delete_ledger_entries as _delete_ledger_entries,
                update_daily_balance as _update_daily_balance,
            )
            _delete_ledger_entries(
                reference_ids=[adjustment.id],
                reference_type='SecurityDepositAdjustment',
            )
            _update_daily_balance(
                shop=booking.shop_id,
                account=adjustment.account_id,
                date=adjustment.created_at.date(),
            )

        adjustment.delete()

        transaction.savepoint_commit(savepoint)

        # -------- Create Activity Log --------
        try:
            log_message = (
                f"Security deposit {adjustment.adjustment_type} of \u20b9{deleted_amount:.2f} deleted."
            )

            if adjustment.payment_method:
                log_message += f" Method: {adjustment.payment_method.upper()}."

            if adjustment.reason:
                log_message += f" Reason: {adjustment.reason}."

            create_activity_log(
                shop=booking.shop,
                user=request.user,
                instance=booking,
                action="deleted",
                message=log_message
            )

        except Exception as log_error:
            logger.exception(
                f"Failed to create activity log for security adjustment deletion "
                f"for booking {booking.id}: {log_error}"
            )

        return self.success_response(
            data={"deleted_amount": float(deleted_amount)},
            message="Adjustment deleted successfully."
        )

    except SecurityDepositAdjustment.DoesNotExist:
        transaction.savepoint_rollback(savepoint)
        return self.not_found_response(resource="Adjustment")

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.exception("Adjustment delete failed")
        return self.error_response(message="Failed to delete adjustment", dev_message=str(e))


__all__ = ["delete_security_adjustment"]
