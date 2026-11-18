import calendar
import logging
from decimal import Decimal

from django.db import DatabaseError
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils.timezone import localtime
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound

from purchase.models.purchase import Purchase

logger = logging.getLogger("api_requests")

# Vendor ids are BigAutoField - anything larger would overflow Postgres BIGINT.
MAX_BIGINT = 9223372036854775807
MIN_YEAR, MAX_YEAR = 2020, 2030
DATE_FORMAT = "%d-%m-%Y %H:%M:%S"

# Only these columns are read - no model instances, no relations (no N+1).
ORDER_FIELDS = (
    "id",
    "purchase_code",
    "invoice_number",
    "purchase_date",
    "created_at",
    "purchase_total",
    "paid_amount",
)

INVALID = "invalid"
OUT_OF_RANGE = "out_of_range"

# (message for end user, dev_message for frontend developer) per input/error kind.
VALIDATION_MESSAGES = {
    "vendor_id": {
        INVALID: ("Invalid vendor ID provided.", "vendor_id must be a positive integer."),
        OUT_OF_RANGE: ("Invalid vendor ID provided.", "vendor_id must be a positive integer."),
    },
    "year": {
        INVALID: ("Year must be a valid number.", "Query param 'year' must be an integer, e.g. 2025."),
        OUT_OF_RANGE: ("Year must be between 2020 and 2030.", "Query param 'year' must be an integer from 2020 to 2030."),
    },
    "month": {
        INVALID: ("Month must be a valid number.", "Query param 'month' must be an integer, e.g. 10."),
        OUT_OF_RANGE: ("Month must be between 1 and 12.", "Query param 'month' must be an integer from 1 to 12."),
    },
}

PAGE_NOT_FOUND_MESSAGE = "The requested page is not available."
PAGE_NOT_FOUND_DEV_MESSAGE = "Query param 'page' must be a positive integer not greater than total_pages."
SERVER_ERROR_MESSAGE = "Unable to fetch monthly summary. Please try again."
SERVER_ERROR_DEV_MESSAGE = (
    "The vendor monthly summary could not be loaded due to a server error. "
    "Retry; if it persists, report it with the request time."
)


def _parse_int_param(raw, *, min_value, max_value):
    """
    Returns (value, error_kind). Blank/missing input -> (None, None), i.e.
    "not provided"; error_kind is INVALID or OUT_OF_RANGE on failure.
    """
    if raw is None or str(raw).strip() == "":
        return None, None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, INVALID
    if not min_value <= value <= max_value:
        return None, OUT_OF_RANGE
    return value, None


def _month_start(dt):
    """First instant of dt's month in the active timezone (Asia/Kolkata)."""
    return localtime(dt).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_key(month_start):
    return f"{calendar.month_name[month_start.month]} {month_start.year}"


def _filtered_totals(queryset):
    """
    Per-month and overall SUMs for every purchase matching the applied filters
    (not just the current page), in one grouped query - at most one row per
    month. Overall figures are summed from the same rows, so they always agree
    with the month figures. TruncMonth runs in the active timezone, matching
    _month_start.

    Returns (month_totals keyed by month start, overall_total, overall_paid).
    """
    rows = list(
        queryset.annotate(month=TruncMonth("purchase_date"))
        .values("month")
        .annotate(total=Sum("purchase_total"), paid_total=Sum("paid_amount"))
        .order_by()
    )
    overall_total = sum((row["total"] or Decimal("0") for row in rows), Decimal("0"))
    overall_paid = sum((row["paid_total"] or Decimal("0") for row in rows), Decimal("0"))
    return {row["month"]: row for row in rows}, overall_total, overall_paid


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Vendor monthly purchase summary",
    description=(
        "Returns a vendor's purchases grouped month-wise by purchase date, with full-month totals "
        "and an overall summary for the applied filters.\n\n"
        "Purpose: Gives shop management a month-by-month ledger of what was bought from a vendor "
        "and how much of it has been paid, plus the overall amount and paid amount for the selected "
        "period, without paging through the full purchase list.\n\n"
        "Context: Used from a vendor's detail screen in the active shop. Mirrors the Tailor monthly "
        "summary response structure. Grouping, filtering and ordering use purchase_date only - "
        "created_at is returned for reference and is never used to group or filter. Months and the "
        "purchases inside them are ordered newest first. Pagination applies to purchases; each month's "
        "total and paid_total always cover the whole month, even when its purchases span several pages.\n\n"
        "Overall summary: overall_summary covers every purchase matching the applied year/month filters "
        "(not just the current page) and is identical on every page. With no filter it covers all of the "
        "vendor's purchases; with year it covers that year; with year and month it covers that month only; "
        "with month alone it covers that month in every year. monthly_summary is narrowed by the same filters.\n\n"
        "Authentication: JWT Bearer token and the X-Active-Shop-ID header are required. "
        "Only shop owners and managers can access this endpoint.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- vendor_id (integer >= 1): the vendor whose purchases are summarised.\n\n"
        "Query parameters - Optional:\n"
        "- year (integer, 2020-2030): only purchases whose purchase_date falls in this year. Default: all years.\n"
        "- month (integer, 1-12): only purchases whose purchase_date falls in this month. "
        "Without year, matches this month in every year. Default: all months.\n"
        "- page (integer >= 1): page number. Default: 1.\n"
        "- page_size (integer, 1-100): purchases per page. Default: 15."
    ),
    parameters=[
        OpenApiParameter(
            name="vendor_id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the vendor whose purchases are summarised. The vendor must belong "
                "to the active shop and must not be deleted. "
                "Location: URL path parameter. "
                "Format/Type: positive integer (e.g. 12). "
                "Required."
            ),
        ),
        OpenApiParameter(
            name="X-Active-Shop-ID",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.HEADER,
            required=True,
            description=(
                "Purpose: Identifies the shop the request is made for; the vendor and its purchases "
                "are looked up only inside this shop. "
                "Location: request header. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
        OpenApiParameter(
            name="year",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Restricts both overall_summary and monthly_summary to purchases whose "
                "purchase_date is in this year (Asia/Kolkata time). "
                "Location: query parameter. "
                "Format/Type: integer between 2020 and 2030 (e.g. 2025). "
                "Optional. Default: all years. An empty value is treated as not provided."
            ),
        ),
        OpenApiParameter(
            name="month",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Restricts both overall_summary and monthly_summary to purchases whose "
                "purchase_date is in this month (Asia/Kolkata time). When year is omitted, the month "
                "is matched across all years. "
                "Location: query parameter. "
                "Format/Type: integer between 1 and 12 (e.g. 10 for October). "
                "Optional. Default: all months. An empty value is treated as not provided."
            ),
        ),
        OpenApiParameter(
            name="page",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Selects which page of purchases to return. "
                "Location: query parameter. "
                "Format/Type: integer >= 1, not greater than total_pages. "
                "Optional. Default: 1."
            ),
        ),
        OpenApiParameter(
            name="page_size",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Number of purchases per page. "
                "Location: query parameter. "
                "Format/Type: integer between 1 and 100; larger values are capped at 100. "
                "Optional. Default: 15."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns a page of the vendor's purchases grouped by month.\n\n"
                "Response shape:\n"
                "- status (string): 'success'.\n"
                "- message (string): 'Data retrieved successfully'.\n"
                "- data.next / data.previous (string | null): URLs of the next / previous page.\n"
                "- data.count (integer): total purchases matching the filters.\n"
                "- data.page_size, data.current_page, data.total_pages, data.start_index, data.end_index (integer).\n"
                "- data.data.overall_summary (object): totals for every purchase matching the applied "
                "year/month filters, identical on every page; 0.0 values when there are no purchases.\n"
                "  - overall_total_amount (number): sum of purchase_total of the filtered purchases.\n"
                "  - overall_total_paid_amount (number): sum of paid_amount of the filtered purchases.\n"
                "- data.data.monthly_summary (object): keyed by month label 'Month YYYY' (e.g. 'October 2025'), "
                "newest first; only months with purchases on the current page; empty object when there are "
                "no purchases. Each month contains:\n"
                "  - total (number): sum of purchase_total of all the vendor's purchases in that month.\n"
                "  - paid_total (number): sum of paid_amount of all the vendor's purchases in that month.\n"
                "  - vendor_orders (array, newest first): id (integer), purchase_code (string), "
                "invoice_number (string | null), purchase_date (string, DD-MM-YYYY HH:MM:SS), "
                "created_at (string, DD-MM-YYYY HH:MM:SS, informational only), "
                "purchase_total (number), paid_amount (number)."
            )
        ),
        400: OpenApiResponse(
            description=(
                "Validation error (status 'validation_error'). Exact messages: "
                "Invalid vendor ID provided.; "
                "Year must be between 2020 and 2030.; "
                "Year must be a valid number.; "
                "Month must be between 1 and 12.; "
                "Month must be a valid number."
            )
        ),
        401: OpenApiResponse(
            description="Authentication error. The JWT Bearer token is missing, invalid or expired."
        ),
        403: OpenApiResponse(
            description=(
                "Permission error. Exact messages include: "
                "Only shop management can perform this action.; "
                "Shop does not have an active subscription.; "
                "Your access to this shop has been deactivated."
            )
        ),
        404: OpenApiResponse(
            description=(
                "Not found (status 'not_found'). Exact messages: "
                "Vendor not found. (vendor does not exist, belongs to another shop, or is deleted); "
                "The requested page is not available. (page is not a positive integer or is greater than total_pages)"
            )
        ),
        500: OpenApiResponse(
            description="Server error (status 'error'). Exact message: Unable to fetch monthly summary. Please try again."
        ),
    },
    examples=[
        OpenApiExample(
            "Monthly summary",
            value={
                "status": "success",
                "message": "Data retrieved successfully",
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
                        "overall_summary": {
                            "overall_total_amount": 22700.0,
                            "overall_total_paid_amount": 14200.0,
                        },
                        "monthly_summary": {
                            "October 2025": {
                                "total": 18500.0,
                                "paid_total": 10000.0,
                                "vendor_orders": [
                                    {
                                        "id": 241,
                                        "purchase_code": "PO000241",
                                        "invoice_number": "INV-7781",
                                        "purchase_date": "18-10-2025 11:30:00",
                                        "created_at": "18-10-2025 11:42:05",
                                        "purchase_total": 12000.0,
                                        "paid_amount": 10000.0,
                                    },
                                    {
                                        "id": 236,
                                        "purchase_code": "PO000236",
                                        "invoice_number": None,
                                        "purchase_date": "02-10-2025 09:00:00",
                                        "created_at": "05-10-2025 16:10:44",
                                        "purchase_total": 6500.0,
                                        "paid_amount": 0.0,
                                    },
                                ],
                            },
                            "September 2025": {
                                "total": 4200.0,
                                "paid_total": 4200.0,
                                "vendor_orders": [
                                    {
                                        "id": 230,
                                        "purchase_code": "PO000230",
                                        "invoice_number": "INV-7702",
                                        "purchase_date": "29-09-2025 15:00:00",
                                        "created_at": "03-10-2025 10:05:12",
                                        "purchase_total": 4200.0,
                                        "paid_amount": 4200.0,
                                    }
                                ],
                            },
                        },
                    },
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Filtered by year and month (?year=2025&month=10)",
            value={
                "status": "success",
                "message": "Data retrieved successfully",
                "data": {
                    "next": None,
                    "previous": None,
                    "count": 2,
                    "page_size": 15,
                    "current_page": 1,
                    "total_pages": 1,
                    "start_index": 1,
                    "end_index": 2,
                    "data": {
                        "overall_summary": {
                            "overall_total_amount": 18500.0,
                            "overall_total_paid_amount": 10000.0,
                        },
                        "monthly_summary": {
                            "October 2025": {
                                "total": 18500.0,
                                "paid_total": 10000.0,
                                "vendor_orders": [
                                    {
                                        "id": 241,
                                        "purchase_code": "PO000241",
                                        "invoice_number": "INV-7781",
                                        "purchase_date": "18-10-2025 11:30:00",
                                        "created_at": "18-10-2025 11:42:05",
                                        "purchase_total": 12000.0,
                                        "paid_amount": 10000.0,
                                    },
                                    {
                                        "id": 236,
                                        "purchase_code": "PO000236",
                                        "invoice_number": None,
                                        "purchase_date": "02-10-2025 09:00:00",
                                        "created_at": "05-10-2025 16:10:44",
                                        "purchase_total": 6500.0,
                                        "paid_amount": 0.0,
                                    },
                                ],
                            },
                        },
                    },
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "No purchases",
            value={
                "status": "success",
                "message": "Data retrieved successfully",
                "data": {
                    "next": None,
                    "previous": None,
                    "count": 0,
                    "page_size": 15,
                    "current_page": 1,
                    "total_pages": 1,
                    "start_index": 0,
                    "end_index": 0,
                    "data": {
                        "overall_summary": {
                            "overall_total_amount": 0.0,
                            "overall_total_paid_amount": 0.0,
                        },
                        "monthly_summary": {},
                    },
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Invalid month",
            value={
                "status": "validation_error",
                "message": "Month must be between 1 and 12.",
                "data": None,
                "dev_message": "Query param 'month' must be an integer from 1 to 12.",
            },
            response_only=True,
            status_codes=["400"],
        ),
        OpenApiExample(
            "Vendor not found",
            value={
                "status": "not_found",
                "message": "Vendor not found.",
                "data": None,
                "dev_message": "Vendor '999' not found for this shop.",
            },
            response_only=True,
            status_codes=["404"],
        ),
        OpenApiExample(
            "Page not available",
            value={
                "status": "not_found",
                "message": PAGE_NOT_FOUND_MESSAGE,
                "data": None,
                "dev_message": PAGE_NOT_FOUND_DEV_MESSAGE,
            },
            response_only=True,
            status_codes=["404"],
        ),
    ],
)
@action(
    detail=False,
    methods=["get"],
    url_path=r"monthly-summary/(?P<vendor_id>\d+)",
)
def vendor_monthly_summary(self, request, vendor_id=None):
    params = {
        "vendor_id": _parse_int_param(vendor_id, min_value=1, max_value=MAX_BIGINT),
        "year": _parse_int_param(request.query_params.get("year"), min_value=MIN_YEAR, max_value=MAX_YEAR),
        "month": _parse_int_param(request.query_params.get("month"), min_value=1, max_value=12),
    }
    for name, (_, error_kind) in params.items():
        if error_kind:
            message, dev_message = VALIDATION_MESSAGES[name][error_kind]
            return self.validation_error_response(message=message, dev_message=dev_message)

    vendor_id, year, month = (params[name][0] for name in ("vendor_id", "year", "month"))

    try:
        vendor, error = self._get_vendor_or_404(vendor_id)
        if error:
            return error

        purchases = Purchase.objects.filter(
            shop_id=self.shop_id,
            vendor_id=vendor.id,
            deleted__isnull=True,
        )
        if year is not None:
            purchases = purchases.filter(purchase_date__year=year)
        if month is not None:
            purchases = purchases.filter(purchase_date__month=month)

        # -id breaks purchase_date ties so rows never repeat/skip across pages.
        rows = purchases.order_by("-purchase_date", "-id").values(*ORDER_FIELDS)
        try:
            page = self.paginator.paginate_queryset(rows, request, view=self)
        except NotFound:
            return self.not_found_response(
                resource="Page",
                message=PAGE_NOT_FOUND_MESSAGE,
                dev_message=PAGE_NOT_FOUND_DEV_MESSAGE,
            )

        grouped = {}
        overall_total = overall_paid = Decimal("0")
        if page:
            totals, overall_total, overall_paid = _filtered_totals(purchases)

            # Rows are already newest-first, so insertion order is newest month first.
            for row in page:
                month_start = _month_start(row["purchase_date"])
                key = _month_key(month_start)
                if key not in grouped:
                    month_totals = totals.get(month_start, {})
                    grouped[key] = {
                        "total": float(month_totals.get("total") or 0),
                        "paid_total": float(month_totals.get("paid_total") or 0),
                        "vendor_orders": [],
                    }
                grouped[key]["vendor_orders"].append({
                    "id": row["id"],
                    "purchase_code": row["purchase_code"],
                    "invoice_number": row["invoice_number"],
                    "purchase_date": localtime(row["purchase_date"]).strftime(DATE_FORMAT),
                    "created_at": localtime(row["created_at"]).strftime(DATE_FORMAT),
                    "purchase_total": float(row["purchase_total"]),
                    "paid_amount": float(row["paid_amount"]),
                })

        return self.paginator.get_paginated_response({
            # Covers every purchase matching the year/month filters, on every page.
            "overall_summary": {
                "overall_total_amount": float(overall_total),
                "overall_total_paid_amount": float(overall_paid),
            },
            "monthly_summary": grouped,
        })

    except DatabaseError:
        logger.exception(
            "Vendor monthly summary failed: shop_id=%s vendor_id=%s year=%s month=%s",
            self.shop_id, vendor_id, year, month,
        )
        return self.error_response(
            message=SERVER_ERROR_MESSAGE,
            dev_message=SERVER_ERROR_DEV_MESSAGE,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


__all__ = ["vendor_monthly_summary"]
