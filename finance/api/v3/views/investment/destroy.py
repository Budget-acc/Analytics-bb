import logging

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.utils import (
    delete_ledger_entries as _delete_ledger_entries,
    get_available_balance_as_of,
)
from finance.models import Investment

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Delete an investment entry",
    description=(
        "Soft-deletes an investment entry. If the entry was completed (i.e. had posted a ledger "
        "inflow entry), that ledger entry is removed and the affected account's daily balance is "
        "recalculated. Before deleting a completed investment, the linked account's current "
        "available balance is checked - if the account no longer holds enough balance to absorb "
        "removing this investment's inflow (e.g. the funds were already spent or transferred out), "
        "the deletion is blocked."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Investment ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Investment deleted successfully."),
        400: OpenApiResponse(
            description=(
                "Validation error. Exact message: "
                "Insufficient balance in this account to delete this investment. Available balance: ₹<available>."
            )
        ),
        404: OpenApiResponse(description="Exact message: Investment not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the investment."),
    },
)
def destroy(self, request, pk=None):
    try:
        try:
            investment = Investment.objects.get(pk=pk, shop_id=self.shop_id)
        except Investment.DoesNotExist:
            return self.not_found_response(
                resource="Investment",
                message="Investment not found.",
                dev_message=f"Investment with id {pk} not found for shop {self.shop_id}.",
            )

        with transaction.atomic():
            if investment.status == 'completed':
                if investment.account_id:
                    # Lock the account row first so a concurrent delete against
                    # the same account serializes instead of both reading a
                    # stale balance - same locking pattern as withdrawal/create.py.
                    available = get_available_balance_as_of(
                        investment.account_id, timezone.localdate(), lock_for_update=True
                    )
                    if investment.amount > available:
                        message = (
                            f'Insufficient balance in this account to delete this investment. '
                            f'Available balance: ₹{available}.'
                        )
                        return self.validation_error_response(
                            errors={'amount': [message]},
                            message=message,
                        )
                # Ledger cleanup must run whenever the investment was
                # completed, regardless of whether its account link still
                # exists - delete_ledger_entries() resolves everything from
                # reference_id/reference_type alone, so skipping this when
                # account_id is None (e.g. the account was later deleted)
                # would leave an orphaned LedgerEntry behind.
                _delete_ledger_entries(reference_ids=[investment.id], reference_type='Investment')
            investment.delete()

        return self.success_response(
            data=None,
            message="Investment deleted successfully.",
        )

    except Exception as e:
        logger.error(f"Investment destroy error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the investment.",
            dev_message=str(e),
        )


__all__ = ["destroy"]
