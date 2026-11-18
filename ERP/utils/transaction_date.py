from datetime import datetime

from django.utils.timezone import now, make_aware, localtime

DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d")
DATETIME_FORMATS = (
    "%d-%m-%YT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d-%m-%YT%H:%M",
    "%Y-%m-%dT%H:%M",
)

INVALID_FORMAT_ERROR = (
    "Invalid format. Use DD-MM-YYYY or YYYY-MM-DD, optionally with time THH:MM[:SS]."
)


def _parse(value, formats):
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_optional_transaction_date(value, min_date=None, min_label="booking", return_date_only=False):
    """
    Parse an optional historical transaction date from the request body.

    Accepted: DD-MM-YYYY, YYYY-MM-DD, each optionally followed by THH:MM or THH:MM:SS.
    - Date + time -> that exact moment (local timezone).
    - Date only   -> that date combined with the current local time (never midnight).
    Missing / null / blank value -> no date, so callers keep their existing behaviour.

    Returns (aware_datetime | None, error | None); error is a dict of kwargs for
    validation_error_response(). With return_date_only=True returns
    (aware_datetime | None, is_date_only, error | None).
    """
    transaction_dt, is_date_only, error = _parse_transaction_date(value, min_date, min_label)
    if return_date_only:
        return transaction_dt, is_date_only, error
    return transaction_dt, error


def _parse_transaction_date(value, min_date, min_label):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, False, None

    if not isinstance(value, str):
        return None, False, {
            "errors": {"date": [INVALID_FORMAT_ERROR]},
            "message": "Invalid refund date format.",
        }

    value = value.strip()
    parsed = _parse(value, DATETIME_FORMATS)
    is_date_only = False

    if parsed is None:
        parsed_date = _parse(value, DATE_FORMATS)
        if parsed_date is None:
            return None, False, {
                "errors": {"date": [INVALID_FORMAT_ERROR]},
                "message": "Invalid refund date format.",
            }
        parsed = datetime.combine(parsed_date.date(), localtime(now()).time())
        is_date_only = True

    transaction_dt = make_aware(parsed)

    # Reject future dates; date-only values compare by date so "today" is always valid
    current = now()
    is_future = (
        transaction_dt.date() > localtime(current).date()
        if is_date_only
        else transaction_dt > current
    )
    if is_future:
        return None, is_date_only, {
            "errors": {"date": ["date cannot be a future date."]},
            "message": "Date cannot be a future date.",
        }

    # Refund cannot predate the booking/order it belongs to
    if min_date and transaction_dt.date() < localtime(min_date).date():
        message = f"Refund date cannot be earlier than the {min_label} date."
        return None, is_date_only, {
            "errors": {"date": [message]},
            "message": message,
        }

    return transaction_dt, is_date_only, None
