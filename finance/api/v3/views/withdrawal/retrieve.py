import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.serializers.withdrawal_serializer import WithdrawalReadSerializer
from finance.models import Withdrawal

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Retrieve a withdrawal entry",
    description="Fetches a single withdrawal entry by ID for the active shop.",
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Withdrawal ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Withdrawal retrieved successfully."),
        404: OpenApiResponse(description="Exact message: Withdrawal not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the withdrawal."),
    },
)
def retrieve(self, request, pk=None):
    try:
        withdrawal = (
            Withdrawal.objects
            .select_related('account', 'category')
            .get(pk=pk, shop_id=self.shop_id)
        )
        return self.success_response(
            data=WithdrawalReadSerializer(withdrawal).data,
            message="Withdrawal retrieved successfully.",
        )
    except Withdrawal.DoesNotExist:
        return self.not_found_response(
            resource="Withdrawal",
            message="Withdrawal not found.",
            dev_message=f"Withdrawal with id {pk} not found for shop {self.shop_id}.",
        )
    except Exception as e:
        logger.error(f"Withdrawal retrieve error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the withdrawal.",
            dev_message=str(e),
        )


__all__ = ["retrieve"]
