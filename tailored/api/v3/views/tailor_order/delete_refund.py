import logging
from decimal import Decimal
from django.db import transaction
from rest_framework.decorators import action
from tailored.models import TailorOrder, TailorOrderRefund
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import (
    OpenApiExample, OpenApiParameter, OpenApiResponse,
    OpenApiTypes, extend_schema,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Delete refund from tailor order",
    description=(
        "Deletes a refund record for a tailor order and adds the refunded amount back "
        "to the order's paid amount."
    ),
    parameters=[
        OpenApiParameter(
            "order_id", OpenApiTypes.INT, OpenApiParameter.PATH,
            required=True, description="Tailor Order ID."
        ),
        OpenApiParameter(
            "refund_id", OpenApiTypes.INT, OpenApiParameter.PATH,
            required=True, description="TailorOrderRefund ID."
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Refund deleted successfully."),
        400: OpenApiResponse(
            description=(
                "Exact error messages:\n"
                "- Invalid identifiers provided.\n"
                "- Cannot delete refund for a completed order.\n"
                "- Cannot delete refund: total paid would exceed total payable."
            )
        ),
        404: OpenApiResponse(
            description=(
                "Exact error messages:\n"
                "- Refund not found for this order.\n"
                "- Tailor Order not found."
            )
        ),
        500: OpenApiResponse(description="Exact message: Failed to delete refund. Please try again later."),
    },
    examples=[
        OpenApiExample(
            "Delete refund success",
            value={
                "status": "success",
                "message": "Refund deleted successfully.",
                "data": {
                    "refund_id": 1,
                    "order_id": 5,
                    "shop_tailor_id": "TO000005",
                    "refunded_amount_removed": "500.00",
                    "updated_paid_amount": "1500.00",
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
@action(
    detail=False,
    methods=["delete"],
    url_path=r"delete-refund/(?P<order_id>[^/.]+)/(?P<refund_id>[^/.]+)",
)
@transaction.atomic
def delete_refund(self, request, *args, **kwargs):
    """
    Delete a refund record from a tailor order.

    URL: /tailor-order/delete-refund/{order_id}/{refund_id}/  (DELETE)

    - Adds the refunded amount back to the order's paid_amount.
    - Disallowed for completed orders.
    - Reverses ledger entry if the refund used an account.
    - Uses select_for_update to prevent race conditions.
    """
    order_id = kwargs.get("order_id")
    refund_id = kwargs.get("refund_id")

    # -------- Validate IDs --------
    try:
        order_id = int(order_id)
        refund_id = int(refund_id)
    except (ValueError, TypeError):
        return self.validation_error_response(
            errors={"ids": ["order_id and refund_id must be integers."]},
            message="Invalid identifiers provided.",
        )

    try:
        # -------- Fetch & lock refund (validates it belongs to this shop) --------
        refund_locked = (
            TailorOrderRefund.objects
            .select_related("order")
            .select_for_update()
            .filter(
                id=refund_id,
                order_id=order_id,
                order__shop_id=self.shop_id,
                order__deleted__isnull=True,
            )
            .first()
        )
        if not refund_locked:
            return self.not_found_response(
                resource="Refund",
                message="Refund not found for this order.",
                dev_message=f"TailorOrderRefund {refund_id} not found for order {order_id} in shop {self.shop_id}.",
            )

        # -------- Lock the order independently --------
        try:
            order_locked = TailorOrder.objects.select_for_update().get(
                id=order_id,
                shop_id=self.shop_id,
                deleted__isnull=True,
            )
        except TailorOrder.DoesNotExist:
            return self.not_found_response(
                resource="Tailor Order",
                message="Tailor Order not found.",
                dev_message=f"TailorOrder {order_id} not found during locking.",
            )

        # -------- Business validation --------
        if order_locked.status == "completed":
            return self.validation_error_response(
                errors={"order_status": ["Cannot modify refunds for a completed order."]},
                message="Cannot delete refund for a completed order.",
                dev_message=f"TailorOrder {order_id} status=completed.",
            )

        removed_amount = Decimal(str(refund_locked.refunded_amount or 0))

        # -------- Total Paid must never exceed Total Payable --------
        total_payable = Decimal(str(order_locked.customer_total_charge or 0)) - Decimal(str(order_locked.discount_amount or 0))
        simulated_paid = Decimal(str(order_locked.paid_amount or 0)) + removed_amount
        if simulated_paid > total_payable:
            return self.validation_error_response(
                errors={"refund": ["Deleting this refund would make the total paid amount exceed the total payable amount."]},
                message="Cannot delete refund: total paid would exceed total payable.",
                dev_message=(
                    f"TailorOrder {order_id}: simulated_paid={simulated_paid} > total_payable={total_payable}"
                ),
            )

        # -------- Reverse ledger entry (account-based flow) --------
        if refund_locked.account_id:
            from finance.api.v3.utils import (
                delete_ledger_entries as _delete_ledger_entries,
                update_daily_balance as _update_daily_balance,
            )
            _delete_ledger_entries(
                reference_ids=[refund_locked.id],
                reference_type="TailorOrderRefund",
            )
            _update_daily_balance(
                shop=order_locked.shop_id,
                account=refund_locked.account_id,
                date=refund_locked.created_at.date(),
            )

        # -------- Delete refund record --------
        refund_locked.delete()

        # -------- Add refunded amount back to paid_amount --------
        order_locked.paid_amount = Decimal(str(order_locked.paid_amount or 0)) + removed_amount
        order_locked.save(update_fields=["paid_amount"])

        # -------- Activity log --------
        try:
            create_activity_log(
                shop=order_locked.shop,
                user=request.user,
                instance=order_locked,
                action="refund_deleted",
                message=f"Refund of ₹{removed_amount} deleted for order {order_locked.shop_tailor_id}.",
            )
        except Exception:
            logger.exception("Failed to create activity log for tailor order refund (delete)")

        return self.success_response(
            data={
                "refunded_amount_removed": str(removed_amount),
                "updated_paid_amount": str(order_locked.paid_amount),
            },
            message="Refund deleted successfully.",
            dev_message=f"Deleted refund {refund_id} for TailorOrder {order_id}; restored={removed_amount}",
        )

    except Exception as e:
        logger.exception(
            f"[delete_refund] Failed deleting refund {refund_id} for TailorOrder {order_id}: {e}"
        )
        return self.error_response(
            message="Failed to delete refund. Please try again later.",
            dev_message=str(e),
        )


__all__ = ["delete_refund"]
