import logging
from django.db import transaction
from rest_framework.decorators import action
from tailored.models import TailorOrder, TailorOrderSecurityDepositAdjustment
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Delete security deposit adjustment from tailor order",
    description=(
        "Deletes a security deposit adjustment entry. "
        "For refunded entries with an account, the corresponding ledger entry is also reversed "
        "and daily balances are recalculated. "
        "Deducted entries are removed without any ledger changes."
    ),
    parameters=[
        OpenApiParameter(
            name="adjustment_id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description="Security Deposit Adjustment ID.",
        )
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Adjustment deleted successfully."),
        400: OpenApiResponse(description="Exact message: Order already completed."),
        404: OpenApiResponse(description="Adjustment not found."),
        500: OpenApiResponse(description="Exact message: Failed to delete adjustment."),
    },
)
@action(
    detail=False,
    methods=["delete"],
    url_path=r"delete-security-adjustment/(?P<adjustment_id>[^/.]+)",
)
@transaction.atomic
def delete_security_adjustment(self, request, *args, **kwargs):
    adjustment_id = kwargs.get("adjustment_id")
    savepoint = transaction.savepoint()

    try:
        # -------- Fetch adjustment --------
        try:
            adjustment = TailorOrderSecurityDepositAdjustment.objects.select_related(
                "order"
            ).get(
                id=adjustment_id,
                order__shop_id=self.shop_id,
                order__deleted__isnull=True,
            )
        except TailorOrderSecurityDepositAdjustment.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.not_found_response(resource="Adjustment")

        # -------- Lock the order --------
        order = TailorOrder.objects.select_for_update().get(id=adjustment.order_id)

        # -------- Guard: completed order --------
        if order.status == "completed":
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"status": ["Cannot delete adjustment for a completed order."]},
                message="Order already completed.",
            )

        deleted_amount = adjustment.amount

        # -------- Reverse ledger for refunded entries with account --------
        if adjustment.adjustment_type == "refunded" and adjustment.account_id:
            from finance.api.v3.utils import (
                delete_ledger_entries as _delete_ledger_entries,
                update_daily_balance as _update_daily_balance,
            )
            _delete_ledger_entries(
                reference_ids=[adjustment.id],
                reference_type="TailorOrderSecurityDepositAdjustment",
            )
            _update_daily_balance(
                shop=order.shop_id,
                account=adjustment.account_id,
                date=adjustment.created_at.date(),
            )

        adjustment.delete()

        transaction.savepoint_commit(savepoint)

        # -------- Activity log --------
        try:
            log_msg = (
                f"Security deposit {adjustment.adjustment_type} of "
                f"₹{deleted_amount:.2f} deleted."
            )
            if adjustment.payment_method:
                log_msg += f" Method: {adjustment.payment_method.upper()}."
            if adjustment.reason:
                log_msg += f" Reason: {adjustment.reason}."

            create_activity_log(
                shop=order.shop,
                user=request.user,
                instance=order,
                action="deleted",
                message=log_msg,
            )
        except Exception as log_err:
            logger.exception(
                f"Failed to create activity log for security adjustment deletion "
                f"(TailorOrder {order.id}): {log_err}"
            )

        return self.success_response(
            data={"deleted_amount": float(deleted_amount)},
            message="Adjustment deleted successfully.",
        )

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.exception(
            f"[delete_security_adjustment] Failed for adjustment {adjustment_id}: {str(e)}"
        )
        return self.error_response(
            message="Failed to delete adjustment.",
            dev_message=str(e),
        )


__all__ = ["delete_security_adjustment"]
