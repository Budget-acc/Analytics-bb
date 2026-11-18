import io
import logging

from django.http import FileResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from shop.models import Shop

from .helpers.earnings_excel import build_excel_filename, build_summary, write_staff_earnings_excel

logs = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V4"],
    summary="Download staff earnings Excel report (V4)",
    description=(
        "Generates the monthly Excel report for one staff member and returns the .xlsx file "
        "directly (Content-Disposition: attachment).\n\n"
        "The report uses exactly the same filters, dates and earnings calculations as the "
        "earnings-report and monthly-transactions APIs:\n"
        "- Summary: bookings, sales and custom work counts and earnings, total earnings, and "
        "pending bookings count/amount - the same figures as earnings-report. The Bookings, "
        "Sales and Custom Work rows are left out when their count and amount are both 0.\n"
        "- Transactions: Sl. No., Date, Invoice ID, Transaction Type (Booking / Sales / Custom "
        "Work), Amount - earliest date first. Date is the field each type is filtered by: "
        "bookings and custom work use completed_at, sales use sale_date. Amount is the "
        "per-record earning the summary adds up (bookings: advance amount; sales: total - "
        "discount, min 0; custom work: paid amount).\n\n"
        "Inputs are query parameters (staff_id, month, year) with the same validation "
        "messages as monthly-transactions. In Postman use 'Send and Download'. Error "
        "responses (400/404/500) are JSON."
    ),
    parameters=[
        OpenApiParameter("staff_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Staff id."),
        OpenApiParameter("month", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Month (1-12)."),
        OpenApiParameter("year", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Year (2000-2100)."),
    ],
    responses={
        (200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): OpenApiResponse(
            response=OpenApiTypes.BINARY,
            description=(
                "The .xlsx file, e.g. Content-Disposition: attachment; "
                "filename=\"Demo_Shop_John_Doe_Earnings_Jul-2026.xlsx\""
            ),
        ),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Please provide valid request data.\n"
                "- Invalid data format.\n"
                "- Staff ID is required.\n"
                "- Month is required.\n"
                "- Month must be between 1 and 12.\n"
                "- Year is required.\n"
                "- Year must be between 2000 and 2100.\n"
                "- Invalid numeric values provided."
            )
        ),
        404: OpenApiResponse(description="Exact message: Staff member not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while exporting the staff earnings Excel."),
    },
)
@action(detail=False, methods=["get"], url_path="earnings-excel")
def export_earnings_excel(self, request):
    """
    GET /api/v4/shop/staff-earnings/earnings-excel/?staff_id=10&month=7&year=2026
    Returns the .xlsx file directly.
    """
    staff_id = None
    try:
        staff_id = request.query_params.get("staff_id")
        month = request.query_params.get("month")
        year = request.query_params.get("year")

        # Same validation and messages as monthly-transactions.
        validation_errors = {}
        if not staff_id:
            validation_errors["staff_id"] = ["Staff ID is required."]

        if not month:
            validation_errors["month"] = ["Month is required."]
        elif not (1 <= int(month) <= 12):
            validation_errors["month"] = ["Month must be between 1 and 12."]

        if not year:
            validation_errors["year"] = ["Year is required."]
        elif not (2000 <= int(year) <= 2100):
            validation_errors["year"] = ["Year must be between 2000 and 2100."]

        if validation_errors:
            return self.validation_error_response(
                errors=validation_errors,
                message="Please provide valid request data.",
                dev_message="Validation failed for staff earnings Excel request",
            )

        staff = self._validate_staff_exists(staff_id)
        if not staff:
            return self.not_found_response(
                resource="Staff",
                message="Staff member not found.",
                dev_message=f"Staff {staff_id} not found in shop {self.shop_id}",
            )

        staff_id, month, year = staff.id, int(month), int(year)

        # Same functions the earnings-report API uses.
        summary = build_summary(
            self._get_staff_bookings_data(staff_id, year, month),
            self._get_staff_sales_data(staff_id, year, month),
            self._get_staff_tailor_data(staff_id, year, month),
            self._get_pending_bookings_data(staff_id, year, month),
        )
        rows = self._get_staff_monthly_transaction_rows(staff_id, year, month)
        shop_name = Shop.objects.only("id", "name").get(id=self.shop_id).name

        output = io.BytesIO()
        write_staff_earnings_excel(output, shop_name, staff.name, year, month, summary, rows)
        output.seek(0)

        # Same response construction as expenses_v2 v3 export_excel.
        filename = build_excel_filename(shop_name, staff.name, year, month)
        response = FileResponse(
            output,
            as_attachment=True,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    except ValueError as e:
        return self.validation_error_response(
            errors={"data": ["Invalid numeric values provided."]},
            message="Invalid data format.",
            dev_message=f"ValueError in staff earnings Excel: {str(e)}",
        )

    except Exception as e:
        logs.error(f"Staff earnings Excel error for staff {staff_id}: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while exporting the staff earnings Excel.",
            dev_message=f"{type(e).__name__}: {e}",
        )
