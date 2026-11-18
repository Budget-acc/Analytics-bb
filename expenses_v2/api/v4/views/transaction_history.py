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
        tags=["Expense Management V4"],
        summary="Get paid transaction history",
        description=(
            "Returns merged paid-flow transaction history for the requested date range. "
            "Supports use cases like payment audit, expense reconciliation, and client-level paid history."
        ),
        parameters=[
            OpenApiParameter(
                name="from_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Required. Start date. Format: YYYY-MM-DD or DD-MM-YYYY.",
            ),
            OpenApiParameter(
                name="to_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Required. End date. Format: YYYY-MM-DD or DD-MM-YYYY.",
            ),
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                default="all",
                description=(
                    "Optional comma-separated transaction types. Examples: all, payments, expense, pendings, bookings, sales, security."
                ),
            ),
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Optional client filter.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Exact message: Transaction history fetched successfully."
            ),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Dates are required.\n"
                    "- Invalid date range: Start date cannot be later than end date.\n"
                    "- Client not found."
                )
            ),
            500: OpenApiResponse(
                description=(
                    "Server errors. Exact message:\n"
                    "- Something went wrong while fetching shop transaction history."
                )
            ),
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
