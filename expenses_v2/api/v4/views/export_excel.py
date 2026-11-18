import io
import logging

import xlsxwriter
from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.number_format import INDIAN_EXCEL_NUM_FORMAT
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from .mixins import ShopTransactionExportMixin
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class ExportTransactionsExcelView(ShopTransactionExportMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Expense Management V4"],
        summary="Export paid transactions as Excel",
        description=(
            "Exports paid-flow transactions to Excel for reporting workflows. "
            "Adapts columns for sale rows and supports paid history filters."
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
                description="Optional comma-separated transaction types. Default: all.",
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
            200: OpenApiResponse(description="Excel file response."),
            400: OpenApiResponse(
                description=(
                    "Validation error. Exact message:\n"
                    "- Invalid input. Please check your filters and try again."
                )
            ),
            500: OpenApiResponse(
                description=(
                    "Server error. Exact message:\n"
                    "- Something went wrong while exporting transactions as Excel."
                )
            ),
        },
    )
    def get(self, request):
        try:
            transactions = self.get_transaction_data(request)

            has_sales = any(txn["type"] == "sale" for txn in transactions)

            output = io.BytesIO()
            wb = xlsxwriter.Workbook(output, {'in_memory': True})
            ws = wb.add_worksheet("Transactions")

            ws.set_column(0, 0, 15)  # "Date" column
            ws.set_column(1, 1, 10)  # "Type" column
            ws.set_column(2, 2, 20)  # "Name" column
            ws.set_column(3, 3, 12)  # "Amount" column
            ws.set_column(4, 4, 30)  # "Description" column
            if has_sales:
                ws.set_column(5, 5, 40)  # Items (for sales only)

            center_format = wb.add_format({'align': 'center', 'valign': 'vcenter'})
            money_format = wb.add_format({'align': 'center', 'valign': 'vcenter', 'num_format': INDIAN_EXCEL_NUM_FORMAT})
            headers = ["Date", "Type", "Name", "Amount", "Description"]
            if has_sales:
                headers.append("Items")
            for col, header in enumerate(headers):
                ws.write(0, col, header, center_format)

            for row, txn in enumerate(transactions, start=1):
                ws.write(row, 0, txn["date"], center_format)
                ws.write(row, 1, txn["type"].title(), center_format)
                ws.write(row, 2, txn["name"] or "", center_format)
                try:
                    ws.write_number(row, 3, float(txn["amount"]), money_format)
                except (TypeError, ValueError):
                    ws.write(row, 3, txn["amount"], center_format)
                ws.write(row, 4, txn["description"], center_format)

                if has_sales and txn["type"] == "sale":
                    items_str = txn.get("items", "") if txn.get("items") else ""
                    ws.write(row, 5, items_str, center_format)

            wb.close()
            output.seek(0)
            return FileResponse(output, as_attachment=True, filename="transactions.xlsx")

        except ValueError as ve:
            return self.validation_error_response(
                errors={"detail": [str(ve)]},
                message="Invalid input. Please check your filters and try again."
            )
        except Exception as e:
            logger.error(f"ExportTransactionsExcelView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while exporting transactions as Excel.",
                dev_message=str(e)
            )
