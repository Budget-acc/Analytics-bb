import logging
from collections import OrderedDict
from datetime import datetime

from booking_v2.models import Client

from .helpers import DayGroupedPagination
from .helpers.bookings import _bookings_dates, _fetch_bookings
from .helpers.custom_work import _custom_work_dates, _fetch_custom_work
from .helpers.pending import _pending_dates, _fetch_pending, _total_pending_amount
from .helpers.sales import _sales_dates, _fetch_sales
from .helpers.security import _security_dates, _fetch_security

logger = logging.getLogger("api_requests")

VALID_SECTIONS = {"bookings", "pending", "sales", "security", "custom_work"}


class FinancialDashboardRetrieveMixin:

    def list(self, request, *args, **kwargs):
        try:
            client = _resolve_client(request, self.shop_id)
            section = _parse_section(request)

            # ── Pass 1: collect distinct dates for the requested section ─
            date_set = _get_dates(section, self.shop_id, client)
            # pending: ascending (nearest due date first); all others: newest first
            all_dates = sorted(date_set, reverse=(section != "pending"))

            paginator = DayGroupedPagination()

            if not all_dates:
                paginator.paginate_day_grouped(OrderedDict(), request)
                return paginator.get_paginated_response(OrderedDict())

            # ── Paginate date list (6 dates / page) ──────────────────────
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

            # ── Pass 2: fetch data for the page dates ─────────────────────
            section_data = _fetch_section(section, self.shop_id, client, page_dates)

            # Grand total for pending — sum across ALL dates, not just this page
            total_pendings = (
                _total_pending_amount(self.shop_id, client)
                if section == "pending" else None
            )

            final = _build_response(page_date_strs, section, section_data, total_pendings)
            return paginator.get_paginated_response(final)

        except ValueError as e:
            return self.validation_error_response(
                errors={"detail": [str(e)]},
                message=str(e),
            )
        except Exception as e:
            logger.error(f"FinancialDashboard error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve dashboard data.",
                dev_message=f"{type(e).__name__}: {e}",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Request helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_client(request, shop_id):
    client_id = request.query_params.get("client_id")
    if not client_id:
        return None
    if not client_id.isdigit():
        raise ValueError("Client ID must be a valid integer.")
    try:
        return Client.objects.get(id=client_id, shop_id=shop_id)
    except Client.DoesNotExist:
        raise ValueError("Client not found for this shop.")


def _parse_section(request):
    """Accept exactly one section param. Defaults to 'bookings'. Raises on invalid value."""
    raw = (request.query_params.get("sections") or "").strip()
    if raw and raw not in VALID_SECTIONS:
        raise ValueError(f"Invalid section '{raw}'. Valid options: {sorted(VALID_SECTIONS)}")
    return raw if raw in VALID_SECTIONS else "bookings"


# ─────────────────────────────────────────────────────────────────────────────
# Dispatchers
# ─────────────────────────────────────────────────────────────────────────────

def _get_dates(section, shop_id, client):
    if section == "bookings":
        return _bookings_dates(shop_id, client)
    if section == "pending":
        return _pending_dates(shop_id, client)
    if section == "sales":
        return _sales_dates(shop_id, client)
    if section == "security":
        return _security_dates(shop_id, client)
    if section == "custom_work":
        return _custom_work_dates(shop_id, client)
    return set()


def _fetch_section(section, shop_id, client, page_dates):
    if section == "bookings":
        return _fetch_bookings(shop_id, client, page_dates)
    if section == "pending":
        return _fetch_pending(shop_id, client, page_dates)
    if section == "sales":
        return _fetch_sales(shop_id, client, page_dates)
    if section == "security":
        return _fetch_security(shop_id, client, page_dates)
    if section == "custom_work":
        return _fetch_custom_work(shop_id, client, page_dates)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Response builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_response(page_date_strs, section, section_data, total_pendings=None):
    """
    Returns Month → Date grouped structure.
    For 'pending', prepends a 'total_pendings' key (grand total across all dates).
    """
    months = OrderedDict()

    for date_str in page_date_strs:
        date_obj = datetime.strptime(date_str, "%d-%m-%Y").date()
        month_str = date_obj.strftime("%B %Y")
        raw = section_data.get(date_obj, {})
        items = raw.get("items", [])

        if section == "bookings":
            total = raw.get("total", 0)
            accounts = raw.get("accounts", [])
        elif section == "pending":
            # Skip days that have no pending items
            if not items:
                continue
            total = raw.get("total_pending", 0)
        elif section == "sales":
            total = raw.get("total", 0)
            accounts = raw.get("accounts", [])
        elif section == "security":
            total = raw.get("total_security", 0)
            accounts = raw.get("accounts", [])
        elif section == "custom_work":
            total = raw.get("total", 0)
            accounts = raw.get("accounts", [])
        else:
            total = 0
            accounts = []

        if month_str not in months:
            months[month_str] = OrderedDict()

        day_entry = {"total": total, section: items}
        if section != "pending":
            day_entry["accounts"] = accounts
        if section == "security":
            day_entry["total_pending"] = raw.get("total_pending", 0)
            day_entry["total_deducted"] = raw.get("total_deducted", 0)
            day_entry["total_refunded"] = raw.get("total_refunded", 0)
        if section == "custom_work":
            day_entry["total_amount_with_security"] = raw.get("total_amount_with_security", 0)

        months[month_str][date_str] = day_entry

    if section == "pending":
        final = OrderedDict()
        final["total_pendings"] = total_pendings or 0
        final.update(months)
        return final

    return months
