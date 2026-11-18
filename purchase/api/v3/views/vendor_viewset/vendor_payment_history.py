import logging

from django.db import DatabaseError
from django.db.models import Count, Sum
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
from purchase.models.purchase_payment import PurchasePayment

# Shared with the monthly summary so both endpoints validate ids and label
# months identically.
from .vendor_monthly_summary import (
    DATE_FORMAT,
    MAX_BIGINT,
    PAGE_NOT_FOUND_DEV_MESSAGE,
    PAGE_NOT_FOUND_MESSAGE,
    VALIDATION_MESSAGES,
    _filtered_totals,
    _month_key,
    _month_start,
    _parse_int_param,
)

logger = logging.getLogger("api_requests")

# Only these columns are read; purchase/account fields come via the JOIN in
# .values(), so no model instances and no N+1.
PAYMENT_FIELDS = (
    "id",
    "amount",
    "payment_date",
    "payment_method",
    "transaction_reference",
    "account_label",
    "account_id",
    "account__name",
    "purchase_id",
    "purchase__purchase_code",
)

SERVER_ERROR_MESSAGE = "Unable to fetch payment history. Please try again."
SERVER_ERROR_DEV_MESSAGE = (
    "The vendor payment history could not be loaded due to a server error. "
    "Retry; if it persists, report it with the request time."
)


def _payment_account_name(row):
    """Linked finance.Account name, falling back to the legacy free-text label."""
    return row["account__name"] if row["account_id"] else row["account_label"]


def _monthly_payment_totals(payments):
    """
    Full-month paid total and payment count for every month this vendor has
    payments in (not just the current page), in one grouped query. TruncMonth
    runs in the active timezone, matching _month_start.
    """
    rows = (
        payments.annotate(month=TruncMonth("payment_date"))
        .values("month")
        .annotate(total_paid=Sum("amount"), payment_count=Count("id"))
        .order_by()
    )
    return {row["month"]: row for row in rows}


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Vendor purchase payment history",
    description=(
        "Returns every payment made against a vendor's purchases, grouped month-wise by payment date, "
        "plus an overall purchase / paid / remaining summary for the vendor.\n\n"
        "Purpose: Gives shop management a payment ledger for one vendor - when each payment was made, "
        "from which account, against which purchase - and how much is still owed, without opening "
        "each purchase.\n\n"
        "Context: Used from a vendor's detail screen in the active shop. Grouping and ordering use "
        "payment_date only (newest first), never created_at or purchase_date - a payment made in "
        "October for a September purchase appears under October. Pagination applies to payments; each "
        "month's total_paid and payment_count always cover the whole month, even when its payments span "
        "several pages. Each month also carries total_purchase: the purchase_total of the vendor's purchases "
        "whose purchase_date falls in that month (what was bought that month, next to what was paid that "
        "month). overall_summary covers all of the vendor's (non-deleted) purchases and is "
        "identical on every page. Amounts are based on purchase_total (additional charges are excluded, "
        "matching the purchase outstanding balance). remaining_amount = total_purchase_amount - "
        "total_paid_amount and is negative if the vendor has been overpaid. Payments on deleted "
        "purchases are excluded.\n\n"
        "Authentication: JWT Bearer token and the X-Active-Shop-ID header are required. "
        "Only shop owners and managers can access this endpoint.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- vendor_id (integer >= 1): the vendor whose payment history is returned.\n\n"
        "Query parameters - Optional:\n"
        "- page (integer >= 1): page number. Default: 1.\n"
        "- page_size (integer, 1-100): payments per page. Default: 15."
    ),
    parameters=[
        OpenApiParameter(
            name="vendor_id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the vendor whose payment history is returned. The vendor must "
                "belong to the active shop and must not be deleted. "
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
                "Purpose: Identifies the shop the request is made for; the vendor, its purchases and "
                "payments are looked up only inside this shop. "
                "Location: request header. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
        OpenApiParameter(
            name="page",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Selects which page of payments to return. "
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
                "Purpose: Number of payments per page. "
                "Location: query parameter. "
                "Format/Type: integer between 1 and 100; larger values are capped at 100. "
                "Optional. Default: 15."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns a page of the vendor's payments grouped by month, plus the overall summary.\n\n"
                "Response shape:\n"
                "- status (string): 'success'.\n"
                "- message (string): 'Data retrieved successfully'.\n"
                "- data.next / data.previous (string | null): URLs of the next / previous page.\n"
                "- data.count (integer): total payments for the vendor.\n"
                "- data.page_size, data.current_page, data.total_pages, data.start_index, data.end_index (integer).\n"
                "- data.data.overall_summary (object), identical on every page:\n"
                "  - total_purchase_amount (number): sum of purchase_total of the vendor's purchases.\n"
                "  - total_paid_amount (number): sum of paid_amount of the vendor's purchases.\n"
                "  - remaining_amount (number): total_purchase_amount - total_paid_amount (negative if overpaid).\n"
                "- data.data.monthly_summary (object): keyed by month label 'Month YYYY' (e.g. 'October 2025') "
                "of payment_date, newest first; only months with payments on the current page; empty object "
                "when there are no payments. Each month contains:\n"
                "  - total_purchase (number): sum of purchase_total of the vendor's purchases whose "
                "purchase_date is in that month (0.0 when nothing was purchased that month).\n"
                "  - total_paid (number): sum of all of the vendor's payments in that month.\n"
                "  - payment_count (integer): number of the vendor's payments in that month.\n"
                "  - payments (array, newest first): id (integer), amount (number), payment_date (string, "
                "DD-MM-YYYY HH:MM:SS), payment_method (string: cash, upi, bank, cheque), "
                "transaction_reference (string | null), account_id (integer | null - null for legacy "
                "payments without a linked account), account_name (string | null - the linked account's "
                "name, or the stored account label for legacy payments), "
                "purchase_id (integer), purchase_code (string)."
            )
        ),
        400: OpenApiResponse(
            description="Validation error (status 'validation_error'). Exact message: Invalid vendor ID provided."
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
            description=f"Server error (status 'error'). Exact message: {SERVER_ERROR_MESSAGE}"
        ),
    },
    examples=[
        OpenApiExample(
            "Payment history",
            value={
                "status": "success",
                "message": "Data retrieved successfully",
                "data": {
                    "next": None,
                    "previous": None,
                    "count": 4,
                    "page_size": 15,
                    "current_page": 1,
                    "total_pages": 1,
                    "start_index": 1,
                    "end_index": 4,
                    "data": {
                        "overall_summary": {
                            "total_purchase_amount": 30700.0,
                            "total_paid_amount": 19200.0,
                            "remaining_amount": 11500.0,
                        },
                        "monthly_summary": {
                            "October 2025": {
                                "total_purchase": 18500.0,
                                "total_paid": 15000.0,
                                "payment_count": 2,
                                "payments": [
                                    {
                                        "id": 88,
                                        "amount": 10000.0,
                                        "payment_date": "20-10-2025 16:45:00",
                                        "payment_method": "upi",
                                        "transaction_reference": "UPI-55120931",
                                        "account_id": 4,
                                        "account_name": "HDFC UPI",
                                        "purchase_id": 241,
                                        "purchase_code": "PO000241",
                                    },
                                    {
                                        "id": 85,
                                        "amount": 5000.0,
                                        "payment_date": "03-10-2025 11:10:00",
                                        "payment_method": "cash",
                                        "transaction_reference": None,
                                        "account_id": 1,
                                        "account_name": "Cash in Hand",
                                        "purchase_id": 180,
                                        "purchase_code": "PO000180",
                                    },
                                ],
                            },
                            "September 2025": {
                                "total_purchase": 4200.0,
                                "total_paid": 4200.0,
                                "payment_count": 1,
                                "payments": [
                                    {
                                        "id": 80,
                                        "amount": 4200.0,
                                        "payment_date": "30-09-2025 18:00:00",
                                        "payment_method": "bank",
                                        "transaction_reference": "NEFT-7781",
                                        "account_id": None,
                                        "account_name": "Old SBI account",
                                        "purchase_id": 230,
                                        "purchase_code": "PO000230",
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
            "Purchases but no payments yet",
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
                            "total_purchase_amount": 6500.0,
                            "total_paid_amount": 0.0,
                            "remaining_amount": 6500.0,
                        },
                        "monthly_summary": {},
                    },
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Invalid vendor id",
            value={
                "status": "validation_error",
                "message": "Invalid vendor ID provided.",
                "data": None,
                "dev_message": "vendor_id must be a positive integer.",
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
    ],
)
@action(
    detail=False,
    methods=["get"],
    url_path=r"payment-history/(?P<vendor_id>\d+)",
)
def vendor_payment_history(self, request, vendor_id=None):
    # The route only matches digits (\d+), so the only reachable failure is an
    # out-of-range id (0 or beyond BIGINT).
    vendor_id, error_kind = _parse_int_param(vendor_id, min_value=1, max_value=MAX_BIGINT)
    if error_kind:
        message, dev_message = VALIDATION_MESSAGES["vendor_id"][error_kind]
        return self.validation_error_response(message=message, dev_message=dev_message)

    try:
        vendor, error = self._get_vendor_or_404(vendor_id)
        if error:
            return error

        # Per-purchase-month totals and the overall summary from the same
        # grouped query over all of the vendor's live purchases (1 query), so
        # the month figures and the overall figures always agree.
        purchase_month_totals, total_purchase, total_paid = _filtered_totals(
            Purchase.objects.filter(
                shop_id=self.shop_id,
                vendor_id=vendor.id,
                deleted__isnull=True,
            )
        )

        payments = PurchasePayment.objects.filter(
            purchase__shop_id=self.shop_id,
            purchase__vendor_id=vendor.id,
            purchase__deleted__isnull=True,
        )

        # -id breaks payment_date ties so rows never repeat/skip across pages.
        rows = payments.order_by("-payment_date", "-id").values(*PAYMENT_FIELDS)
        try:
            page = self.paginator.paginate_queryset(rows, request, view=self)
        except NotFound:
            return self.not_found_response(
                resource="Page",
                message=PAGE_NOT_FOUND_MESSAGE,
                dev_message=PAGE_NOT_FOUND_DEV_MESSAGE,
            )

        grouped = {}
        if page:
            month_totals = _monthly_payment_totals(payments)

            # Rows are already newest-first, so insertion order is newest month first.
            for row in page:
                month_start = _month_start(row["payment_date"])
                key = _month_key(month_start)
                if key not in grouped:
                    month = month_totals.get(month_start, {})
                    purchased = purchase_month_totals.get(month_start, {})
                    grouped[key] = {
                        "total_purchase": float(purchased.get("total") or 0),
                        "total_paid": float(month.get("total_paid") or 0),
                        "payment_count": month.get("payment_count") or 0,
                        "payments": [],
                    }
                grouped[key]["payments"].append({
                    "id": row["id"],
                    "amount": float(row["amount"]),
                    "payment_date": localtime(row["payment_date"]).strftime(DATE_FORMAT),
                    "payment_method": row["payment_method"],
                    "transaction_reference": row["transaction_reference"],
                    "account_id": row["account_id"],
                    "account_name": _payment_account_name(row),
                    "purchase_id": row["purchase_id"],
                    "purchase_code": row["purchase__purchase_code"],
                })

        return self.paginator.get_paginated_response({
            "overall_summary": {
                "total_purchase_amount": float(total_purchase),
                "total_paid_amount": float(total_paid),
                "remaining_amount": float(total_purchase - total_paid),
            },
            "monthly_summary": grouped,
        })

    except DatabaseError:
        logger.exception(
            "Vendor payment history failed: shop_id=%s vendor_id=%s",
            self.shop_id, vendor_id,
        )
        return self.error_response(
            message=SERVER_ERROR_MESSAGE,
            dev_message=SERVER_ERROR_DEV_MESSAGE,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


__all__ = ["vendor_payment_history"]
