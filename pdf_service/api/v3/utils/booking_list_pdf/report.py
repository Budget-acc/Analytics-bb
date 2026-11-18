"""
Turns the filtered bookings into the plain data the Booking List PDF prints.

Amounts follow the existing list/ledger definitions:
- paid    = advance_amount
- pending = max(0, total_amount - advance_amount - discount_amount)
  (the desktop list's `remaining_amount`)
"""
import re
from datetime import date
from decimal import Decimal

from django.utils.timezone import localtime

from .filters import DESKTOP_STATUSES, MOBILE_STATUSES, PLATFORM_DESKTOP, parse_custom_date

ROW_CHUNK_SIZE = 500
_DATE_LABEL = "%d/%m/%Y"
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

STATUS_LABELS = {
    "completed": "Completed", "upcoming": "Upcoming", "future": "Future", "past": "Past",
    "measurements": "Measurements", "returns": "Returns", "pending": "Pending",
    "not_returned": "Not Returned", "cancelled": "Cancelled", "expired": "Expired", "all": "All",
}


def shop_info(shop) -> dict:
    return {
        key: (getattr(shop, key, "") or "")
        for key in ("name", "address", "city", "state", "pincode", "phone", "phone2", "email", "gst_number")
    }


def _amount(value) -> Decimal:
    return Decimal(value or 0)


def collect_rows(queryset):
    """
    (rows, totals) in the queryset's order, streamed in chunks so large lists
    stay memory-bounded (prefetch runs once per chunk - no N+1).
    """
    rows = []
    total_paid = total_pending = Decimal(0)
    for sno, booking in enumerate(queryset.iterator(chunk_size=ROW_CHUNK_SIZE), start=1):
        paid = _amount(booking.advance_amount)
        pending = max(Decimal(0), _amount(booking.total_amount) - paid - _amount(booking.discount_amount))
        client = booking.client_identity
        rows.append({
            "sno": sno,
            "pickup": localtime(booking.pickup_date).strftime("%d/%m/%Y %I:%M %p") if booking.pickup_date else "",
            "invoice_id": booking.shop_booking_id or "",
            "products": [item.product_name for item in getattr(booking, "pdf_items", []) if item.product_name],
            "client_name": client.client_name if client else "",
            "client_phone": str(client.phone_1_e164) if client and client.phone_1_e164 else "",
            "paid": paid,
            "pending": pending,
        })
        total_paid += paid
        total_pending += pending
    return rows, {"count": len(rows), "paid": total_paid, "pending": total_pending}


def status_label(platform, params) -> str:
    """The status the list actually applied: desktop matches case-insensitively,
    mobile matches the exact value; anything else means no status filter."""
    raw = params.get("status") or ""
    if platform == PLATFORM_DESKTOP:
        key, allowed = raw.strip().lower(), DESKTOP_STATUSES
    else:
        key, allowed = raw, MOBILE_STATUSES
    return STATUS_LABELS[key] if key in allowed else "All"


def _label_date(value):
    parsed = parse_custom_date(value.strip()) if isinstance(value, str) and value.strip() else None
    return parsed.strftime(_DATE_LABEL) if parsed else None


def filter_lines(platform, params, generated_at):
    """
    Human-readable lines for the pickup date / purchase mode / search filters
    actually applied (the platform and status are not printed), plus the
    generation time. The filter line is omitted when none of them is used.
    """
    parts = []
    start, end = _label_date(params.get("start_date")), _label_date(params.get("end_date"))
    if start or end:
        parts.append(f"Pickup: {start or '...'} - {end or '...'}")
    if params.get("purchase_mode"):
        parts.append(f"Purchase mode: {str(params['purchase_mode']).strip().title()}")
    if (params.get("search") or "").strip():
        parts.append(f'Search: "{params["search"].strip()}"')
    generated = f"Generated on {localtime(generated_at).strftime('%d/%m/%Y %I:%M %p')}"
    return [" | ".join(parts), generated] if parts else [generated]


def download_filename(platform, params, today: date) -> str:
    """Bookings_Desktop_Upcoming_26-10-2026.pdf (filesystem/header safe)."""
    name = (
        f"Bookings_{'Desktop' if platform == PLATFORM_DESKTOP else 'Mobile'}_"
        f"{status_label(platform, params).replace(' ', '_')}_{today.strftime('%d-%m-%Y')}.pdf"
    )
    return _FILENAME_UNSAFE.sub("_", name)
