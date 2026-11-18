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
        tags=["Expense Management V4"],
        summary="Export paid transactions as PDF",
        description=(
            "Exports paid-flow transactions into a PDF report for accounting and reconciliation. "
            "Includes expenses, payments, pendings, bookings, sales, and security based on filters."
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
                description="Optional comma-separated types. Default: all.",
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
            200: OpenApiResponse(description="PDF file response."),
            400: OpenApiResponse(
                description=(
                    "Validation error. Exact message:\n"
                    "- Invalid input. Please check your filters and try again."
                )
            ),
            500: OpenApiResponse(
                description=(
                    "Server error. Exact message:\n"
                    "- Something went wrong while exporting transactions as PDF."
                )
            ),
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
                # Safely truncate long fields
                name_text = (txn.get('name') or 'N/A')[:20]
                desc_text = (txn.get('description') or '')[:50]
                items_text = (txn.get('items', ''))[:30] if txn.get('items') else ''

                if txn['type'] == 'sale' and items_text:
                    line = f"[{txn['date']}] {txn['type'].title()} | {name_text} | {format_inr_or_raw(txn['amount'])} | {desc_text} | Items: {items_text}"
                else:
                    line = f"[{txn['date']}] {txn['type'].title()} | {name_text} | {format_inr_or_raw(txn['amount'])} | {desc_text}"

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
            logger.error(f"ExportTransactionsPDFView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while exporting transactions as PDF.",
                dev_message=str(e)
            )
