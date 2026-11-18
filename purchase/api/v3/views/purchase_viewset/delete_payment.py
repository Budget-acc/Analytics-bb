import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from purchase.models import Purchase, PurchasePayment
from ERP.utils.shop_activity_logs import create_activity_log
from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries, update_daily_balance as _update_daily_balance

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Delete purchase payment",
    description="Deletes a payment record, reverses its ledger entry, and recalculates the purchase balance.",
    parameters=[
        OpenApiParameter(name="payment_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Payment record ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Payment deleted successfully."),
        400: OpenApiResponse(description="Exact message: Payment cannot be deleted from a completed purchase."),
        404: OpenApiResponse(description="Exact message: Payment record not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the payment."),
    },
)
@action(detail=False, methods=["delete"], url_path="delete-payment/(?P<payment_id>[^/.]+)")
@transaction.atomic
def delete_payment(self, request, *args, **kwargs):
    payment_id = kwargs.get("payment_id")
    try:
        payment = PurchasePayment.objects.select_related("purchase").filter(
            id=payment_id,
            purchase__shop_id=self.shop_id,
            purchase__deleted__isnull=True,
        ).first()
        if not payment:
            return self.not_found_response(resource="Payment", message="Payment record not found.")

        purchase = Purchase.objects.select_for_update().get(id=payment.purchase_id)
        locked_payment = PurchasePayment.objects.select_for_update().get(id=payment_id)

        if purchase.is_completed:
            return self.validation_error_response(
                errors={"is_completed": ["Cannot delete payment from a completed purchase."]},
                message="Payment cannot be deleted from a completed purchase.",
            )

        payment_amount = locked_payment.amount
        account_id = locked_payment.account_id
        # payment_date (not created_at, the row's insertion timestamp) is
        # what the ledger entry was actually stamped with - using the
        # wrong one here would recalculate the wrong day's DailyBalance.
        payment_date = locked_payment.payment_date

        if account_id:
            _delete_ledger_entries(reference_ids=[locked_payment.id], reference_type="PurchasePayment")
            _update_daily_balance(shop=purchase.shop_id, account=account_id, date=payment_date.date())

        locked_payment.delete()
        purchase.refresh_from_db(fields=["paid_amount"])

        try:
            create_activity_log(
                shop=purchase.shop,
                user=request.user,
                instance=purchase,
                action="payment_deleted",
                message=f"Deleted payment of ₹{payment_amount} from purchase {purchase.purchase_code}.",
            )
        except Exception:
            logger.exception("Failed to create activity log for purchase payment deletion")

        return self.success_response(
            data={
                "purchase_id": purchase.id,
                "deleted_payment_amount": payment_amount,
                "paid_amount": purchase.paid_amount,
                "outstanding_balance": purchase.outstanding_balance,
            },
            message="Payment deleted successfully.",
        )

    except Exception as e:
        logger.error(f"Purchase delete_payment error for payment {payment_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the payment.",
            dev_message=str(e),
        )


__all__ = ["delete_payment"]
