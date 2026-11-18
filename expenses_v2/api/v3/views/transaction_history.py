import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from .mixins import ShopTransactionExportMixin
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class ShopTransactionHistoryAPIView(ShopTransactionExportMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Get paid transaction history",
        description=(
            "Returns merged transaction history used by paid flow reporting. "
            "Supports date range, type filters, and optional client filter."
        ),
        parameters=[
            OpenApiParameter(name="from_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=True, description="Required. Format: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="to_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=True, description="Required. Format: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Comma-separated transaction types. Default: all."),
            OpenApiParameter(name="client_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional client filter."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Transaction history fetched successfully."),
            400: OpenApiResponse(
                description=(
                    "Exact validation messages include:\n"
                    "- Dates are required.\n"
                    "- Invalid date range: Start date cannot be later than end date.\n"
                    "- Client not found."
                )
            ),
            403: OpenApiResponse(description="Permission denied response from active shop check."),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching shop transaction history."),
        },
    )
    def get(self, request):
        try:
            transactions = self.get_transaction_data(request)
            return self.success_response(
                data=transactions,
                message="Transaction history fetched successfully."
            )
        except ValueError as ve:
            return self.validation_error_response(
                errors={"detail": [str(ve)]},
                message=str(ve)
            )
        except Exception as e:
            logger.error(f"ShopTransactionHistoryAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching shop transaction history.",
                dev_message=str(e)
            )
