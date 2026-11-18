import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.utils import (
    delete_ledger_entries as _delete_ledger_entries,
    update_daily_balance as _update_daily_balance,
)
from tailored.models import TailorPayment

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Delete a tailor payment",
    description=(
        "Soft-deletes a tailor payment entry made to a tailor.\n\n"
        "Purpose: Lets shop staff reverse a payment that was recorded in error (wrong tailor, "
        "duplicate entry, cancelled payment) so it no longer counts toward the tailor's paid "
        "total or the shop's accounts.\n\n"
        "Context: Used from the tailor payment list/history screen's delete action. Deleting "
        "removes the corresponding ledger outflow entry and recalculates the daily balance for "
        "the affected account and transaction date, so the amount immediately becomes available "
        "again as part of the tailor's remaining pending amount for future payments.\n\n"
        "Accepted inputs:\n"
        "Path parameters — Required:\n"
        "- pk (integer): the tailor payment id to delete."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True,
            description=(
                "Purpose: Identifies which tailor payment record to delete. "
                "Location: URL path parameter. "
                "Format/Type: integer (TailorPayment id). "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Tailor payment deleted successfully."),
        404: OpenApiResponse(description="Exact message: Tailor payment not found."),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the tailor payment."),
    },
)
def destroy(self, request, pk=None):
    try:
        try:
            payment = TailorPayment.objects.get(pk=pk, shop_id=self.shop_id)
        except TailorPayment.DoesNotExist:
            return self.not_found_response(
                resource="TailorPayment",
                message="Tailor payment not found.",
                dev_message=f"TailorPayment with id {pk} not found for shop {self.shop_id}.",
            )

        account_id = payment.account_id
        payment_date = payment.transaction_date_time.date()

        # Ledger removal, daily-balance recalculation, and the payment's own
        # soft-delete must succeed or fail together - otherwise a failure
        # partway through leaves the ledger reversed while the payment row
        # still counts as "paid", silently corrupting the tailor's balance.
        with transaction.atomic():
            _delete_ledger_entries(
                reference_ids=[payment.id],
                reference_type='TailorPayment',
            )

            if account_id:
                _update_daily_balance(
                    shop=payment.shop_id,
                    account=account_id,
                    date=payment_date,
                )

            payment.delete()

        return self.success_response(
            data=None,
            message="Tailor payment deleted successfully.",
        )

    except Exception as e:
        logger.error(f"TailorPayment destroy error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the tailor payment.",
            dev_message=str(e),
        )
