import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
from finance.models import AccountTransfer

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Delete an account transfer",
    description=(
        "Soft-deletes an account transfer and removes both its ledger entries "
        "(transfer_out and transfer_in). Daily balances for both affected accounts "
        "are recalculated automatically."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH,
            required=True, description="Account transfer ID.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Account transfer deleted successfully."),
        404: OpenApiResponse(description="Exact message: Account transfer not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while deleting the transfer."),
    },
)
def destroy(self, request, pk=None):
    try:
        try:
            transfer = AccountTransfer.objects.get(pk=pk, shop_id=self.shop_id)
        except AccountTransfer.DoesNotExist:
            return self.not_found_response(
                resource="AccountTransfer",
                message="Account transfer not found.",
                dev_message=f"AccountTransfer with id {pk} not found for shop {self.shop_id}.",
            )

        _delete_ledger_entries(
            reference_ids=[transfer.id],
            reference_type='AccountTransfer',
        )

        transfer.delete()

        return self.success_response(
            data=None,
            message="Account transfer deleted successfully.",
        )

    except Exception as e:
        logger.error(f"AccountTransfer destroy error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the transfer.",
            dev_message=str(e),
        )
