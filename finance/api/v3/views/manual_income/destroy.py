import logging

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.utils import (
    delete_ledger_entries as _delete_ledger_entries,
    get_available_balance_as_of,
    update_daily_balance as _update_daily_balance,
)
from finance.models import ManualIncome

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Delete a manual income entry",
    description=(
        "Soft-deletes a manual income entry and removes its ledger inflow entry. "
        "The daily balance for the affected account and date is recalculated. "
        "Before deletion, the linked account's current available balance is checked - if the "
        "account no longer holds enough balance to absorb removing this income's inflow (e.g. "
        "the funds were already spent or transferred out), the deletion is blocked."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH,
            required=True, description="Manual income ID.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Manual income deleted successfully."),
        400: OpenApiResponse(
            description=(
                "Validation error. Exact message: "
                "Insufficient balance in this account to delete this manual income. Available balance: ₹<available>."
            )
        ),
        404: OpenApiResponse(description="Exact message: Manual income not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the manual income."),
    },
)
def destroy(self, request, pk=None):
    try:
        try:
            income = ManualIncome.objects.get(pk=pk, shop_id=self.shop_id)
        except ManualIncome.DoesNotExist:
            return self.not_found_response(
                resource="ManualIncome",
                message="Manual income not found.",
                dev_message=f"ManualIncome with id {pk} not found for shop {self.shop_id}.",
            )

        account_id = income.account_id
        income_date = income.transaction_date_time.date()

        with transaction.atomic():
            if account_id:
                # Lock the account row first so a concurrent delete against the
                # same account serializes instead of both reading a stale
                # balance - same locking pattern as withdrawal/create.py.
                available = get_available_balance_as_of(
                    account_id, timezone.localdate(), lock_for_update=True
                )
                if income.amount > available:
                    message = (
                        f'Insufficient balance in this account to delete this manual income. '
                        f'Available balance: ₹{available}.'
                    )
                    return self.validation_error_response(
                        errors={'amount': [message]},
                        message=message,
                    )

            _delete_ledger_entries(
                reference_ids=[income.id],
                reference_type='ManualIncome',
            )

            if account_id:
                _update_daily_balance(
                    shop=income.shop_id,
                    account=account_id,
                    date=income_date,
                )

            income.delete()

        return self.success_response(
            data=None,
            message="Manual income deleted successfully.",
        )

    except Exception as e:
        logger.error(f"ManualIncome destroy error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the manual income.",
            dev_message=str(e),
        )


__all__ = ["destroy"]
