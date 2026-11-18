import logging
from datetime import datetime as _datetime
from decimal import Decimal, InvalidOperation

from django.utils.timezone import now as _now, make_aware as _make_aware, localtime as _localtime

logger = logging.getLogger("api_requests")


def _parse_purchase_date(self, request_data):
    """
    Parse the optional purchase_date field.
    Formats: DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS (same convention as
    Booking's booked_date/payment_date parsing).
    - Not provided at all -> now() (current server datetime).
    - Date only -> combined with the current time.
    - Date + time -> used exactly as given.
    Returns (datetime, error_response).
    """
    raw = request_data.get("purchase_date")
    # Defensive: a QueryDict-derived source could still hand back a
    # single-element list for a scalar field - unwrap rather than 500.
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return _now(), None
    try:
        dt = _make_aware(_datetime.strptime(raw, "%d-%m-%YT%H:%M:%S"))
    except ValueError:
        try:
            parsed_date = _datetime.strptime(raw, "%d-%m-%Y").date()
            dt = _make_aware(_datetime.combine(parsed_date, _localtime(_now()).time()))
        except ValueError:
            return None, self.validation_error_response(
                errors={"purchase_date": ["Invalid format. Use DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."]},
                message="Invalid purchase date format.",
            )
    if dt > _now():
        return None, self.validation_error_response(
            errors={"purchase_date": ["purchase_date cannot be a future date."]},
            message="Purchase date cannot be a future date.",
        )
    return dt, None


def _parse_payment_date(self, request_data, field_name="payment_date"):
    """
    Parse the optional payment_date field.
    Formats: DD-MM-YYYY, YYYY-MM-DD, or either with a time component
    (DD-MM-YYYYTHH:MM:SS / YYYY-MM-DDTHH:MM:SS).
    - Not provided -> now() (current server datetime).
    - Date only -> combined with the current time.
    - Date + time -> used exactly as given.
    No future-date rejection (unlike _parse_purchase_date) - not required
    for payments, which are routinely recorded same-day.
    Returns (datetime, error_response).
    """
    raw = request_data.get(field_name)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw:
        return _now(), None

    for fmt in ("%d-%m-%YT%H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _make_aware(_datetime.strptime(raw, fmt)), None
        except ValueError:
            continue

    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            parsed_date = _datetime.strptime(raw, fmt).date()
            return _make_aware(_datetime.combine(parsed_date, _localtime(_now()).time())), None
        except ValueError:
            continue

    return None, self.validation_error_response(
        errors={field_name: ["Invalid format. Use DD-MM-YYYY, YYYY-MM-DD, or either with THH:MM:SS."]},
        message="Invalid payment date format.",
    )


def _to_decimal(self, value, field_name):
    """Returns (Decimal, error_response)."""
    try:
        dec = Decimal(str(value))
        if dec < 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return None, self.validation_error_response(
            errors={field_name: [f"{field_name} must be a non-negative number."]},
            message=f"Invalid {field_name.replace('_', ' ')}.",
        )
    return dec, None


def _validate_without_products_fields(self, request_data):
    """
    Validate the without-products payload: purchase_total, total_quantity,
    tax_rate, tax_amount. No account is required here - a purchase is only
    ever settled (ledger entry created) when an advance payment is actually
    provided via paid_amount[] (see helpers/payments.py:_process_advance_payments).
    Returns (dict of validated values, error_response).
    """
    purchase_total, err = self._to_decimal(request_data.get("purchase_total"), "purchase_total")
    if err:
        return None, err

    try:
        total_quantity = int(request_data.get("total_quantity"))
        if total_quantity <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return None, self.validation_error_response(
            errors={"total_quantity": ["total_quantity must be a positive integer."]},
            message="Invalid total quantity.",
        )

    tax_rate, err = self._to_decimal(request_data.get("tax_rate", 0), "tax_rate")
    if err:
        return None, err
    tax_amount, err = self._to_decimal(request_data.get("tax_amount", 0), "tax_amount")
    if err:
        return None, err

    return {
        "purchase_total": purchase_total,
        "total_quantity": total_quantity,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
    }, None


def _resolve_account(self, shop, account_id, field_name="account_id"):
    """Returns (Account, payment_method, error_response)."""
    from finance.models import Account

    account = Account.objects.filter(
        id=account_id, shop_id=shop.id, deleted__isnull=True, is_active=True,
    ).first()
    if not account:
        return None, None, self.validation_error_response(
            errors={field_name: ["Invalid or inactive account."]},
            message="Invalid account selected.",
        )
    payment_method = "cash" if account.account_type == "cash" else "upi"
    return account, payment_method, None


__all__ = [
    "_parse_purchase_date",
    "_parse_payment_date",
    "_to_decimal",
    "_validate_without_products_fields",
    "_resolve_account",
]
