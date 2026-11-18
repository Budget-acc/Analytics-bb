import logging
import hashlib
import json
import re
from datetime import datetime, date

logger = logging.getLogger("api_requests")


def _parse_date_for_filename(value):
    """Parse date inputs (isoformat / YYYY-MM-DD / DD-MM-YYYY) into a date."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    raw = str(value).strip()
    if not raw:
        return None

    # Handle ISO datetime-like strings ("2026-02-01T00:00:00")
    if "T" in raw:
        raw = raw.split("T", 1)[0]

    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _format_dmy(value) -> str:
    parsed = _parse_date_for_filename(value)
    return parsed.strftime("%d-%m-%Y") if parsed else ""


def _unique_preserve_order(values):
    seen = set()
    out = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def build_pdf_download_filename(pdf_type: str, request_params: dict) -> str:
    """Build a stable, user-friendly filename like sales_02-12-2025_01-02-2026.pdf."""
    txn_types = request_params.get("txn_types") or request_params.get("type") or ["all"]
    if isinstance(txn_types, str):
        txn_types = [t.strip().lower() for t in txn_types.split(",") if t.strip()]
    else:
        txn_types = [str(t).strip().lower() for t in (txn_types or []) if str(t).strip()]

    if not txn_types:
        txn_types = ["all"]

    txn_types = _unique_preserve_order(txn_types)
    if txn_types == ["tailor_orders"]:
        prefix = "Custom Works"
    elif set(txn_types) == {"payments", "expenses"}:
        prefix = "Profit and Loss"
    elif "all" in txn_types:
        prefix = "ledger" if pdf_type == "ledger_book" else "all"
    else:
        # Canonical ordering so it doesn't depend on query param order
        canonical = ["sales", "payments", "expenses", "bookings", "pendings", "security"]
        rank = {t: i for i, t in enumerate(canonical)}
        known = [t for t in txn_types if t in rank]
        unknown = [t for t in txn_types if t not in rank]
        known.sort(key=lambda t: rank[t])
        unknown.sort()
        prefix = "_".join(known + unknown)

    from_part = _format_dmy(request_params.get("from_date"))
    to_part = _format_dmy(request_params.get("to_date"))
    base = f"{prefix}_{from_part}_{to_part}.pdf" if from_part and to_part else f"{prefix}.pdf"

    # Sanitize for Windows/macOS/Linux filenames
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    return base


def compute_request_hash(params_dict):
    """Compute hash for request deduplication."""
    normalized = json.dumps(params_dict, sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()
