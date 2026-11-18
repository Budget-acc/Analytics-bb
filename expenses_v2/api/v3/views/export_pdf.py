import io
import logging

from django.http import FileResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.number_format import format_inr_or_raw
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from .mixins import ShopTransactionExportMixin
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class ExportTransactionsPDFView(ShopTransactionExportMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]
        
    @extend_schema(
        tags=["Expense Management V3"],
        summary="Export paid transactions as PDF",
        description="Exports filtered transaction history into a PDF report for paid expense/payment auditing.",
        parameters=[
            OpenApiParameter(name="from_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=True, description="Required. Format: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="to_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=True, description="Required. Format: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Comma-separated transaction types. Default: all."),
            OpenApiParameter(name="client_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional client filter."),
        ],
        responses={
            200: OpenApiResponse(description="PDF file download response."),
            400: OpenApiResponse(description="Exact message: Invalid input. Please check your filters and try again."),
            403: OpenApiResponse(description="Permission denied response from active shop check."),
            500: OpenApiResponse(description="Exact message: Something went wrong while exporting transactions as Excel."),
        },
    )
    def get(self, request):
        try:
            transactions = self.get_transaction_data(request)

            buffer = io.BytesIO()
            p = canvas.Canvas(buffer, pagesize=A4)
            y = 800
            p.setFont("Helvetica", 12)
            p.drawString(100, y, "Shop Transaction Report")
            y -= 30

            for txn in transactions:
                user_text = (txn.get('user') or 'N/A')[:20]
                desc_text = (txn.get('description') or '')[:50]

                line = f"[{txn['date']}] {txn['type'].title()} | {user_text} | {format_inr_or_raw(txn['amount'])} | {desc_text}"
                p.drawString(50, y, line)
                y -= 20

                if y < 50:
                    p.showPage()
                    y = 800

            p.save()
            buffer.seek(0)
            return FileResponse(buffer, as_attachment=True, filename="transactions.pdf")

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
