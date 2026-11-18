import logging
from bisect import bisect_left
from collections import defaultdict, OrderedDict
from decimal import Decimal
from datetime import datetime

from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from finance.api.v3.views.financial_dashboard_viewset.helpers.pagination import DayGroupedPagination
from finance.models import DailyBalance, LedgerEntry
from .helpers import batch_enrich

logger = logging.getLogger("api_requests")

ZERO = Decimal("0.00")

# Fallback labels for transfer entries that have no reference object to enrich.
_TRANSFER_LABELS = {
    "transfer_in": "Account Transfer In",
    "transfer_out": "Account Transfer Out",
    "tailor_order_security_in": "Custom Work - Security",
}


class DailySummaryRetrieveMixin:
    """
    Mixin providing the `list` action for DailySummaryViewSet.

    GET /finance/v3/daily-summary/
    GET /finance/v3/daily-summary/?page=2
    GET /finance/v3/daily-summary/?type=income   (direction=in entries only)
    GET /finance/v3/daily-summary/?type=expense  (direction=out entries only)

    Returns a paginated, date-grouped ledger view. Each date includes:
    - Daily balance summary (opening, closing, money_in, money_out) from DailyBalance
    - Per-account closing balances for the day (accounts array)
    - Individual ledger entries enriched with description, client_name, invoice_id
      (booking payments, sale payments, expenses, security in/out, refunds)

    Accessible by all authenticated roles (read-only).
    """

    @extend_schema(
        tags=["Finance Management V3"],
        summary="Get daily summary",
        description=(
            "Returns day-grouped ledger summary with opening/closing balances, money in/out, account breakdown, and enriched entries. "
            "Supports income/expense filtered views."
        ),
        parameters=[
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
            OpenApiParameter(name="type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Optional filter: income or expense."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated daily summary response."),
            403: OpenApiResponse(
                description=(
                    "Permission errors. Exact messages can include:\n"
                    "- You don't have the permission.\n"
                    "- Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(description="Exact message: Unable to retrieve daily summary at this time."),
        },
        examples=[
            OpenApiExample(
                "Request example - income summary",
                description="GET /finance/v3/daily-summary/?type=income&page=1",
                value={
                    "type": "income",
                    "page": 1,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "count": 12,
                    "next": "https://api.example.com/finance/v3/daily-summary/?page=2",
                    "previous": None,
                    "results": {
                        "May-2026": {
                            "04-05-2026": {
                                "opening_balance": "1200.00",
                                "closing_balance": "1800.00",
                                "money_in": "900.00",
                                "money_out": "300.00",
                                "accounts": [
                                    {"name": "Cash in Hand", "amount": "1800.00"}
                                ],
                                "entries": [
                                    {
                                        "id": 101,
                                        "transaction_type": "booking_payment",
                                        "description": "Booking payment received",
                                        "amount": "900.00",
                                        "direction": "in",
                                        "account_name": "Cash in Hand",
                                        "created_at": "04-05-2026 11:15:00"
                                    }
                                ],
                            }
                        }
                    },
                },
                response_only=True,
                status_codes=["200"],
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
                    "message": "Unable to retrieve daily summary at this time.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def list(self, request):
        try:
            # --- 1. All distinct dates with activity (newest first) ---
            all_dates = list(
                DailyBalance.objects.filter(
                    shop_id=self.shop_id,
                    deleted__isnull=True,
                    account__deleted__isnull=True,
                )
                .values_list("date", flat=True)
                .distinct()
                .order_by("-date")
            )

            # Always ensure today is present (even with no entries yet)
            today = timezone.now().date()
            if not all_dates or all_dates[0] != today:
                all_dates = [today] + [d for d in all_dates if d != today]

            paginator = DayGroupedPagination()

            if not all_dates:
                paginator.count = 0
                paginator.request = request
                paginator.grouped_keys = []
                return paginator.get_paginated_response(OrderedDict())

            # --- 2. Paginate over date strings (6 dates per page) ---
            flat_for_pagination = OrderedDict(
                (d.strftime("%d-%m-%Y"), None) for d in all_dates
            )
            paginated_flat = paginator.paginate_day_grouped(flat_for_pagination, request)

            page_date_strs = list(
                paginated_flat.keys()
                if isinstance(paginated_flat, OrderedDict)
                else flat_for_pagination.keys()
            )
            page_dates = [datetime.strptime(d, "%d-%m-%Y").date() for d in page_date_strs]

            # --- 3. DailyBalance → aggregate opening/closing per date + account breakdown ---
            date_summary, account_breakdown = _aggregate_daily_balances(self.shop_id, page_dates)

            # --- 4. LedgerEntry records for the page window ---
            ledger_entries = list(
                LedgerEntry.objects.filter(
                    shop_id=self.shop_id,
                    deleted__isnull=True,
                )
                .annotate(
                    ledger_date=TruncDate(
                        "created_at",
                        tzinfo=timezone.get_current_timezone(),
                    )
                )
                .filter(ledger_date__in=page_dates)
                .select_related("account")
                .order_by("-created_at")
            )

            # --- 5. Enrich entries (client name, invoice, description, main_id) ---
            enrichment = batch_enrich(ledger_entries)

            # --- 6. Group entries by local date, applying optional type filter ---
            type_filter = request.query_params.get("type")  # "income", "expense", or None
            entries_by_date = _group_entries_by_date(ledger_entries, enrichment, type_filter)

            # When a type filter is active, replace the DailyBalance-derived summary
            # with totals computed only from the filtered ledger entries so that
            # money_in / money_out / accounts reflect the selected category only.
            if type_filter:
                date_summary, account_breakdown = _compute_filtered_summary_and_breakdown(
                    ledger_entries, type_filter, page_dates
                )

            # --- 7. Build final Month → Date hierarchy ---
            final_group = _build_response_group(
                page_date_strs, date_summary, entries_by_date, account_breakdown
            )

            logger.info(
                f"DailySummary fetched for shop={self.shop_id}, "
                f"dates={len(page_dates)}, entries={len(ledger_entries)}, type_filter={type_filter}"
            )

            return paginator.get_paginated_response(final_group)

        except Exception as e:
            logger.error(
                f"Error fetching daily summary for shop={self.shop_id}: {e}",
                exc_info=True,
            )
            return self.error_response(
                message="Unable to retrieve daily summary at this time.",
                dev_message=f"Unexpected error in DailySummaryViewSet.list: {type(e).__name__}.",
            )


# ---------------------------------------------------------------------------
# Module-level helpers (stateless, easily testable)
# ---------------------------------------------------------------------------

def _aggregate_daily_balances(shop_id, page_dates):
    """
    Query DailyBalance for the given dates and return a tuple:
        (
            { date_obj: {opening_balance, closing_balance, money_in, money_out} },
            { date_obj: [ {"name": account_name, "amount": closing_balance}, ... ] }
        )
    """
    qs = (
        DailyBalance.objects.filter(
            shop_id=shop_id,
            date__in=page_dates,
            deleted__isnull=True,
            account__deleted__isnull=True,
        )
        .select_related("account")
    )

    summary = defaultdict(lambda: {
        "opening_balance": ZERO,
        "closing_balance": ZERO,
        "money_in": ZERO,
        "money_out": ZERO,
    })
    account_breakdown = defaultdict(list)
    # Track which accounts already have a row for each date.
    date_account_ids = defaultdict(set)

    for db in qs:
        s = summary[db.date]
        s["opening_balance"] += db.opening_balance
        s["closing_balance"] += db.closing_balance
        s["money_in"] += db.total_in
        s["money_out"] += db.total_out
        date_account_ids[db.date].add(db.account_id)

        account_breakdown[db.date].append({
            "name": db.account.name,
            "amount": str(db.closing_balance),
        })

    # For every page date, carry forward the closing balance of accounts that
    # had no activity on that date.  This covers both fully-missing dates (no
    # DailyBalance rows at all) and partially-missing dates (some accounts
    # inactive).  Without this, opening_balance silently drops inactive accounts.
    if page_dates:
        history_rows = list(
            DailyBalance.objects.filter(
                shop_id=shop_id,
                date__lt=max(page_dates),
                deleted__isnull=True,
                account__deleted__isnull=True,
            )
            .select_related("account")
            .order_by("account_id", "date")
        )

        account_history = {}
        for row in history_rows:
            bucket = account_history.setdefault(
                row.account_id,
                {
                    "name": row.account.name,
                    "dates": [],
                    "balances": [],
                },
            )
            bucket["dates"].append(row.date)
            bucket["balances"].append(row.closing_balance)

        for date in page_dates:
            accounts_on_date = date_account_ids.get(date, set())

            for account_id, info in account_history.items():
                if account_id in accounts_on_date:
                    continue  # Already counted from the DailyBalance row

                idx = bisect_left(info["dates"], date) - 1
                if idx < 0:
                    continue  # No history before this date for this account

                carried = info["balances"][idx]
                summary[date]["opening_balance"] += carried
                summary[date]["closing_balance"] += carried
                account_breakdown[date].append({
                    "name": info["name"],
                    "amount": str(carried),
                })

    return summary, account_breakdown


def _group_entries_by_date(ledger_entries, enrichment, type_filter=None):
    """
    Convert LedgerEntry objects → serialisable dicts grouped by local date.
    Returns { date_obj: [entry_dict, ...] }

    type_filter:
        "income"  → include only entries with direction='in'
        "expense" → include only entries with direction='out'
        None      → include all entries
    """
    by_date = defaultdict(list)

    for entry in ledger_entries:
        if type_filter == "income" and entry.direction != "in":
            continue
        if type_filter == "expense" and entry.direction != "out":
            continue

        enrich = enrichment.get(entry.id, {})
        description = (
            enrich.get("description")
            or _TRANSFER_LABELS.get(entry.transaction_type)
            or entry.transaction_type
        )
        entry_dict = {
            "id": entry.id,
            "transaction_type": entry.transaction_type,
            "description": description,
            "amount": str(entry.amount),
            "direction": entry.direction,
            "account_name": entry.account.name if entry.account else None,
            "payment_method": entry.payment_method or None,
            "reference_id": enrich.get("main_id") or entry.reference_id,
            "invoice_id": enrich.get("invoice_id"),
            "client_name": enrich.get("client_name"),
            "items": enrich.get("items", ""),
            "service_id": enrich.get("service_id"),
            "notes": enrich.get("notes") or entry.notes,
            "created_at": timezone.localtime(entry.created_at).strftime("%d-%m-%Y %H:%M:%S"),
        }
        variant_id = enrich.get("variant_id")
        if variant_id is not None:
            entry_dict["variant_id"] = variant_id
        product_id = enrich.get("product_id")
        if product_id is not None:
            entry_dict["product_id"] = product_id
        entry_date = getattr(entry, "ledger_date", timezone.localtime(entry.created_at).date())
        by_date[entry_date].append(entry_dict)

    return by_date


def _compute_filtered_summary_and_breakdown(ledger_entries, type_filter, page_dates):
    """
    When a type filter is active, compute date_summary and account_breakdown
    solely from the matching ledger entries instead of DailyBalance.

    type_filter='income'  → direction='in'  entries only → populates money_in
    type_filter='expense' → direction='out' entries only → populates money_out

    opening_balance and closing_balance are zero because they are running-balance
    concepts that are meaningless for a filtered slice of transactions.
    """
    direction = "in" if type_filter == "income" else "out"

    date_summary = {
        date: {
            "opening_balance": ZERO,
            "closing_balance": ZERO,
            "money_in": ZERO,
            "money_out": ZERO,
        }
        for date in page_dates
    }
    # { date: { account_name: total_amount } }
    account_totals = defaultdict(lambda: defaultdict(Decimal))

    for entry in ledger_entries:
        if entry.direction != direction:
            continue

        entry_date = getattr(entry, "ledger_date", timezone.localtime(entry.created_at).date())
        if entry_date not in date_summary:
            continue

        if direction == "in":
            date_summary[entry_date]["money_in"] += entry.amount
        else:
            date_summary[entry_date]["money_out"] += entry.amount

        account_name = entry.account.name if entry.account else "Unknown"
        account_totals[entry_date][account_name] += entry.amount

    account_breakdown = {
        date: [
            {"name": name, "amount": str(total)}
            for name, total in accounts.items()
        ]
        for date, accounts in account_totals.items()
    }

    return date_summary, account_breakdown


def _build_response_group(page_date_strs, date_summary, entries_by_date, account_breakdown):
    """
    Assemble the final OrderedDict:
        { "April 2026": { "10-04-2026": { summary + accounts + entries } } }
    """
    final_group = OrderedDict()

    for date_str in page_date_strs:
        date_obj = datetime.strptime(date_str, "%d-%m-%Y").date()
        month_str = date_obj.strftime("%B %Y")

        summary = date_summary.get(date_obj, {
            "opening_balance": ZERO,
            "closing_balance": ZERO,
            "money_in": ZERO,
            "money_out": ZERO,
        })

        if month_str not in final_group:
            final_group[month_str] = OrderedDict()

        net_change = summary["money_in"] - summary["money_out"]
        final_group[month_str][date_str] = {
            "opening_balance": str(summary["opening_balance"]),
            "closing_balance": str(summary["closing_balance"]),
            "money_in": str(summary["money_in"]),
            "money_out": str(summary["money_out"]),
            "net_change": str(net_change),
            "accounts": account_breakdown.get(date_obj, []),
            "entries": entries_by_date.get(date_obj, []),
        }

    return final_group
