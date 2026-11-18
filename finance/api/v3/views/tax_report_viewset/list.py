import logging
from datetime import datetime

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.exceptions import ValidationError

from ERP.utils.custom_pagination import StandardPageNumberPagination

from .helpers import build_tax_report_queryset, get_tax_report_summary, group_tax_report_transactions

logger = logging.getLogger("api_requests")

_DATE_FORMATS = ['%Y-%m-%d', '%d-%m-%Y']


def _parse_date(value, param_name):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValidationError({param_name: ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]})


class TaxReportListMixin:

    @extend_schema(
        tags=["Finance Management V3"],
        summary="Get consolidated tax report",
        description=(
            "Returns a paginated tax report — tax-applicable transactions across "
            "Bookings, Sales and Tailor (custom work) Orders for the active shop, "
            "grouped by Month-Year, then Date.\n\n"
            "Purpose: Lets shop owners reconcile tax (e.g. GST) collected across every "
            "revenue stream from a single report, instead of checking each module "
            "separately, with day-wise totals ready for filing.\n\n"
            "Context: Each row is read directly from the tax snapshot saved on the "
            "record at the time tax was calculated (taxable_amount, tax_rate, "
            "tax_amount) — figures are never recomputed here. Only completed, "
            "tax-applied records are included: Bookings with booking_status=completed "
            "(and delivery_status not cancelled), Sales (no status concept — "
            "is_tax_applied is enough), and Tailor Orders with status=completed. "
            "Cancelled and non-completed records are excluded.\n\n"
            "Shop scope is resolved automatically from the active shop context "
            "(X-Active-Shop-ID header) — it is not passed as a query parameter.\n\n"
            "Bookings, Sales and Tailor Orders are first merged into a single "
            "date-descending-ordered list, and pagination is applied to that unified "
            "list (not per-module) — so a single date's records may span two adjacent "
            "pages if they fall across a page boundary; each date's daily_summary in "
            "`transactions` reflects only the rows present on the current page for that "
            "date. For an accurate total across the entire filtered range regardless of "
            "pagination, use the top-level `summary` object instead.\n\n"
            "Accepted inputs:\n"
            "Query parameters — Optional:\n"
            "- start_date: lower bound of the date range.\n"
            "- end_date: upper bound of the date range.\n"
            "- page: pagination page number.\n"
            "- page_size: number of transaction rows per page."
        ),
        parameters=[
            OpenApiParameter(
                name="start_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Lower bound (inclusive) of the transaction date range. "
                    "Compared against completed_at (Bookings, Tailor Orders) or "
                    "sale_date (Sales). "
                    "Location: query parameter. "
                    "Format/Type: date string — YYYY-MM-DD or DD-MM-YYYY. "
                    "Optional. If omitted, no lower bound is applied."
                ),
            ),
            OpenApiParameter(
                name="end_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Upper bound (inclusive) of the transaction date range. "
                    "Must be on or after start_date when both are provided. "
                    "Location: query parameter. "
                    "Format/Type: date string — YYYY-MM-DD or DD-MM-YYYY. "
                    "Optional. If omitted, no upper bound is applied."
                ),
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Purpose: Pagination page number. Location: query parameter. Format/Type: integer. Optional. Default: 1.",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Purpose: Number of results per page. Location: query parameter. Format/Type: integer. Optional. Default: 15. Max: 100.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Returns a paginated tax report split into transactions (grouped "
                    "\"Month YYYY\" → Date) and summary.\n\n"
                    "Response shape:\n"
                    "- data.count (integer): total matching transaction rows across all pages "
                    "(row-level count, not a count of dates).\n"
                    "- data.next / data.previous (string | null): pagination links.\n"
                    "- data.data.transactions (object): \"Month YYYY\" → \"DD-MM-YYYY\" nested "
                    "structure for the CURRENT PAGE's rows only, ordered descending at every "
                    "level. Each date bucket contains:\n"
                    "  - items (array): transaction rows for that date present on this page. "
                    "Each item contains:\n"
                    "    - reference_id (integer): internal database ID of the underlying Booking, Sale or TailorOrder record.\n"
                    "    - date (DD-MM-YYYY): transaction reference date.\n"
                    "    - order_id (string): shop_booking_id / shop_sale_id / shop_tailor_id.\n"
                    "    - type (string): bookings | sales | tailor_orders.\n"
                    "    - taxable_amount (decimal): base amount before tax.\n"
                    "    - tax_rate (decimal): tax percentage applied.\n"
                    "    - tax_amount (decimal): total tax collected.\n"
                    "    - cgst (decimal): tax_amount split equally in two — CGST half.\n"
                    "    - sgst (decimal): tax_amount split equally in two — SGST half.\n"
                    "  - daily_summary (object): taxable_amount, tax_amount, cgst, sgst summed "
                    "across ONLY this date's items on this page. If a date's records span two "
                    "pages, this is a partial total for that page, not the whole day — see "
                    "data.data.summary for accurate full-range totals.\n"
                    "- data.data.summary (object): aggregated totals across ALL rows matching the "
                    "applied start_date/end_date filter (or all-time if neither is given), across "
                    "every page:\n"
                    "  - total_taxable_amount (decimal)\n"
                    "  - total_tax_amount (decimal)\n"
                    "  - total_cgst (decimal): total_tax_amount split equally — CGST half.\n"
                    "  - total_sgst (decimal): total_tax_amount split equally — SGST half."
                )
            ),
            400: OpenApiResponse(
                description=(
                    "Validation error. Exact messages include: "
                    "Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.; "
                    "start_date must be on or before end_date."
                )
            ),
            403: OpenApiResponse(
                description=(
                    "Permission error. Exact message: "
                    "Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching the tax report."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /finance/v3/tax-report/?start_date=2026-07-01&end_date=2026-07-31&page=1",
                value={
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-31",
                    "page": 1,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Retrieved successfully.",
                    "data": {
                        "next": None,
                        "previous": None,
                        "count": 3,
                        "page_size": 15,
                        "current_page": 1,
                        "total_pages": 1,
                        "start_index": 1,
                        "end_index": 3,
                        "data": {
                            "summary": {
                                "total_taxable_amount": "5500.00",
                                "total_tax_amount": "786.00",
                                "total_cgst": "393.00",
                                "total_sgst": "393.00",
                            },
                            "transactions": {
                                "July 2026": {
                                    "20-07-2026": {
                                        "items": [
                                            {
                                                "reference_id": 4821,
                                                "date": "20-07-2026",
                                                "order_id": "000123",
                                                "type": "bookings",
                                                "taxable_amount": "3500.00",
                                                "tax_rate": "18.00",
                                                "tax_amount": "630.00",
                                                "cgst": "315.00",
                                                "sgst": "315.00",
                                            },
                                        ],
                                        "daily_summary": {
                                            "taxable_amount": "3500.00",
                                            "tax_amount": "630.00",
                                            "cgst": "315.00",
                                            "sgst": "315.00",
                                        },
                                    },
                                    "18-07-2026": {
                                        "items": [
                                            {
                                                "reference_id": 902,
                                                "date": "18-07-2026",
                                                "order_id": "TO000045",
                                                "type": "tailor_orders",
                                                "taxable_amount": "1200.00",
                                                "tax_rate": "5.00",
                                                "tax_amount": "60.00",
                                                "cgst": "30.00",
                                                "sgst": "30.00",
                                            },
                                            {
                                                "reference_id": 3310,
                                                "date": "18-07-2026",
                                                "order_id": "SL000210",
                                                "type": "sales",
                                                "taxable_amount": "800.00",
                                                "tax_rate": "12.00",
                                                "tax_amount": "96.00",
                                                "cgst": "48.00",
                                                "sgst": "48.00",
                                            },
                                        ],
                                        "daily_summary": {
                                            "taxable_amount": "2000.00",
                                            "tax_amount": "156.00",
                                            "cgst": "78.00",
                                            "sgst": "78.00",
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid date format response",
                value={
                    "status": "error",
                    "message": "Invalid filter parameters.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Permission denied response",
                value={
                    "status": "permission_denied",
                    "message": "Access to this shop has been deactivated.",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Something went wrong while fetching the tax report.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            raw_start = request.query_params.get("start_date")
            raw_end = request.query_params.get("end_date")

            start_date = _parse_date(raw_start, "start_date") if raw_start else None
            end_date = _parse_date(raw_end, "end_date") if raw_end else None

            if start_date and end_date and start_date > end_date:
                raise ValidationError({"start_date": ["start_date must be on or before end_date."]})

            qs = build_tax_report_queryset(self.shop_id, start_date, end_date)

            paginator = StandardPageNumberPagination()
            page = paginator.paginate_queryset(qs, request)
            results = [
                {
                    "reference_id": row["reference_id"],
                    "date": row["date"],
                    "order_id": row["order_id"],
                    "type": row["type"],
                    "taxable_amount": row["taxable_amount"],
                    "tax_rate": row["tax_rate"],
                    "tax_amount": row["tax_amount"],
                    "cgst": row["tax_amount"] / 2,
                    "sgst": row["tax_amount"] / 2,
                }
                for row in page
            ]

            grouped_transactions = group_tax_report_transactions(results)
            summary = get_tax_report_summary(self.shop_id, start_date, end_date)

            return paginator.get_paginated_response({
                "transactions": grouped_transactions,
                "summary": summary,
            })

        except ValidationError as e:
            return self.validation_error_response(
                errors=e.detail,
                message="Invalid filter parameters.",
            )
        except Exception as e:
            logger.error(f"Tax report list error: {e}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching the tax report.",
                dev_message=str(e),
            )


__all__ = ["TaxReportListMixin"]
