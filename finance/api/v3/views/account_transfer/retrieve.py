import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.serializers.account_transfer_serializer import AccountTransferReadSerializer
from finance.models import AccountTransfer
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Retrieve an account transfer",
    description="Fetches a single account transfer by ID for the active shop.",
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH,
            required=True, description="Account transfer ID.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Account transfer retrieved successfully."),
        404: OpenApiResponse(description="Exact message: Account transfer not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the transfer."),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        transfer = (
            AccountTransfer.objects
            .select_related('from_account', 'to_account', 'created_by')
            .get(pk=pk, shop_id=self.shop_id)
        )
        return self.success_response(
            data=AccountTransferReadSerializer(transfer).data,
            message="Account transfer retrieved successfully.",
        )
    except AccountTransfer.DoesNotExist:
        return self.not_found_response(
            resource="AccountTransfer",
            message="Account transfer not found.",
            dev_message=f"AccountTransfer with id {pk} not found for shop {self.shop_id}.",
        )
    except Exception as e:
        logger.error(f"AccountTransfer retrieve error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the transfer.",
            dev_message=str(e),
        )
