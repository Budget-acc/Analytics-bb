import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
from finance.models import Withdrawal

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Delete a withdrawal entry",
    description=(
        "Soft-deletes a withdrawal entry. If the entry was completed (i.e. had posted a ledger "
        "outflow entry), that ledger entry is removed and the affected account's daily balance is "
        "recalculated. Deleting a withdrawal only ever increases the account's available "
        "balance, so no balance-sufficiency check is required here."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Withdrawal ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Withdrawal deleted successfully."),
        404: OpenApiResponse(description="Exact message: Withdrawal not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the withdrawal."),
    },
)
def destroy(self, request, pk=None):
    try:
        try:
            withdrawal = Withdrawal.objects.get(pk=pk, shop_id=self.shop_id)
        except Withdrawal.DoesNotExist:
            return self.not_found_response(
                resource="Withdrawal",
                message="Withdrawal not found.",
                dev_message=f"Withdrawal with id {pk} not found for shop {self.shop_id}.",
            )

        with transaction.atomic():
            if withdrawal.status == 'completed':
                _delete_ledger_entries(reference_ids=[withdrawal.id], reference_type='Withdrawal')
            withdrawal.delete()

        return self.success_response(
            data=None,
            message="Withdrawal deleted successfully.",
        )

    except Exception as e:
        logger.error(f"Withdrawal destroy error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the withdrawal.",
            dev_message=str(e),
        )


__all__ = ["destroy"]
