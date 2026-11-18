import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.serializers.manual_income_serializer import ManualIncomeReadSerializer
from finance.models import ManualIncome
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Retrieve a manual income entry",
    description="Fetches a single manual income entry by ID for the active shop.",
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH,
            required=True, description="Manual income ID.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Manual income retrieved successfully."),
        404: OpenApiResponse(description="Exact message: Manual income not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the manual income."),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        income = (
            ManualIncome.objects
            .select_related('account', 'category')
            .get(pk=pk, shop_id=self.shop_id)
        )
        return self.success_response(
            data=ManualIncomeReadSerializer(income).data,
            message="Manual income retrieved successfully.",
        )
    except ManualIncome.DoesNotExist:
        return self.not_found_response(
            resource="ManualIncome",
            message="Manual income not found.",
            dev_message=f"ManualIncome with id {pk} not found for shop {self.shop_id}.",
        )
    except Exception as e:
        logger.error(f"ManualIncome retrieve error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the manual income.",
            dev_message=str(e),
        )
