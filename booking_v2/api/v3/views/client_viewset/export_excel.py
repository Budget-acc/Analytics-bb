import io
import logging
from datetime import timedelta

from django.http import FileResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from shop.models import Shop
from trending_insights.api.v3.helpers.date_utils import get_date_range

from .helpers.client_details_excel import build_excel_filename, write_client_details_excel
from .helpers.client_report import client_details_queryset

logger = logging.getLogger("api_requests")

# Generous cap (~10 years) so the full client history can be exported while
# still bounding a single request.
MAX_RANGE_DAYS = 725


@extend_schema(
    tags=["Booking Management V3"],
    summary="Download client details Excel report",
    description=(
        "Returns the active shop's client details as an .xlsx file directly "
        "(Content-Disposition: attachment).\n\n"
        "Same data, columns and order as the Client Details PDF: S.No., Date (client created, "
        "dd/mm/yyyy), Name, Phone Number 1, Phone Number 2 - newest client first. Clients are "
        "grouped under month headings (e.g. 'July 2026'); S.No. is continuous.\n\n"
        "Date range is optional:\n"
        "- Omit both start_date and end_date to export ALL clients (same data as the PDF); "
        "filename <Shop>_Client_Details_All.xlsx.\n"
        "- Send both to include only clients whose created_at falls between them (both "
        "inclusive, Indian time). Range up to 3660 days.\n\n"
        "In Postman use 'Send and Download'. Error responses are JSON."
    ),
    parameters=[
        OpenApiParameter("start_date", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                         description="Optional start date (inclusive). YYYY-MM-DD or DD-MM-YYYY. "
                                     "Send together with end_date; omit both for all clients."),
        OpenApiParameter("end_date", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                         description="Optional end date (inclusive). YYYY-MM-DD or DD-MM-YYYY. "
                                     "Send together with start_date; omit both for all clients."),
    ],
    responses={
        (200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): OpenApiResponse(
            response=OpenApiTypes.BINARY,
            description=(
                "The .xlsx file, e.g. Content-Disposition: attachment; "
                "filename=\"BB_Shop_Client_Details_01-08-2026_to_30-09-2026.xlsx\""
            ),
        ),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- start_date and end_date are required. (only one of them was sent)\n"
                "- start_date and end_date must be valid dates in YYYY-MM-DD or DD-MM-YYYY format.\n"
                "- end_date must be greater than or equal to start_date.\n"
                "- Date range cannot exceed 3660 days."
            )
        ),
        500: OpenApiResponse(description="Exact message: Something went wrong while exporting client details Excel."),
    },
)
@action(detail=False, methods=["get"], url_path="export-excel")
def export_excel(self, request):
    """
    GET /api/v3/bookings/clients/export-excel/                                          -> all clients
    GET /api/v3/bookings/clients/export-excel/?start_date=2026-08-01&end_date=2026-09-30 -> date range
    Returns the .xlsx file directly.
    """
    try:
        start_param = (request.query_params.get("start_date") or "").strip()
        end_param = (request.query_params.get("end_date") or "").strip()

        # No date range -> all clients (same data as the PDF). A range (both
        # dates) filters by created_at; only one of them is a validation error.
        start = end = start_date = end_date = None
        if start_param or end_param:
            try:
                start, end = get_date_range(start_param, end_param, max_days=MAX_RANGE_DAYS)
            except ValueError as e:
                return self.validation_error_response(
                    errors={"date": [str(e)]},
                    message=str(e),
                )
            start_date = start.date()
            end_date = (end - timedelta(days=1)).date()  # end is exclusive

        clients = client_details_queryset(self.shop_id, start, end)
        shop_name = Shop.objects.only("id", "name").get(id=self.shop_id).name

        output = io.BytesIO()
        write_client_details_excel(output, shop_name, start_date, end_date, clients)
        output.seek(0)

        # Same response construction as expenses_v2 v3 export_excel.
        filename = build_excel_filename(shop_name, start_date, end_date)
        response = FileResponse(
            output,
            as_attachment=True,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.error(f"Client details Excel export error for shop {self.shop_id}: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while exporting client details Excel.",
            dev_message=f"{type(e).__name__}: {e}",
        )
