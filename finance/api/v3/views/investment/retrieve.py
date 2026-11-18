import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.serializers.investment_serializer import InvestmentReadSerializer
from finance.models import Investment

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Retrieve an investment entry",
    description="Fetches a single investment entry by ID for the active shop.",
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Investment ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Investment retrieved successfully."),
        404: OpenApiResponse(description="Exact message: Investment not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the investment."),
    },
)
def retrieve(self, request, pk=None):
    try:
        investment = (
            Investment.objects
            .select_related('account', 'category')
            .get(pk=pk, shop_id=self.shop_id)
        )
        return self.success_response(
            data=InvestmentReadSerializer(investment).data,
            message="Investment retrieved successfully.",
        )
    except Investment.DoesNotExist:
        return self.not_found_response(
            resource="Investment",
            message="Investment not found.",
            dev_message=f"Investment with id {pk} not found for shop {self.shop_id}.",
        )
    except Exception as e:
        logger.error(f"Investment retrieve error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the investment.",
            dev_message=str(e),
        )


__all__ = ["retrieve"]
