import calendar
from datetime import datetime, timedelta

from django.utils import timezone

_ACCEPTED_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y")  # YYYY-MM-DD or DD-MM-YYYY

# Max inclusive span for the start_date/end_date filter on the dashboard,
# service-revenue-analytics, trending-products and trending-color-category
# endpoints (Unused Products keeps its own MAX_RANGE_DAYS).
TRENDING_MAX_RANGE_DAYS = 92

# Query values treated as "not sent" (older apps may send literal nulls).
_ABSENT_VALUES = {"", "null", "none"}


def _parse_date(value):
    """Tries each accepted format in turn; returns a date or None. Both
    formats require a 4-digit year in a fixed position (leading for
    YYYY-MM-DD, trailing for DD-MM-YYYY), so a valid string can only ever
    match one of them - never silently parsed under the wrong format."""
    for fmt in _ACCEPTED_DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def get_month_range(month, year):
    """
    Parses/validates `month` (1-12) and `year` query params and returns a
    (start, end) tz-aware datetime pair covering that calendar month, meant
    for `field__gte=start, field__lt=end` filters (never `__year=/__month=`,
    since those can't use a date index the same way).

    Raises ValueError with a user-facing message on invalid/missing input.
    """
    if not month or not year:
        raise ValueError("month and year are required.")

    try:
        month = int(month)
        year = int(year)
    except (TypeError, ValueError):
        raise ValueError("month and year must be valid integers.")

    if not (1 <= month <= 12):
        raise ValueError("month must be between 1 and 12.")
    if year < 2000 or year > 2100:
        raise ValueError("year is out of range.")

    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime(year, month, 1), tz)

    if month == 12:
        end = timezone.make_aware(datetime(year + 1, 1, 1), tz)
    else:
        end = timezone.make_aware(datetime(year, month + 1, 1), tz)

    return start, end


def get_date_range(start_date, end_date, max_days=90):
    """
    Parses/validates start_date/end_date query params - accepting EITHER
    YYYY-MM-DD or DD-MM-YYYY - and returns a (start, end) tz-aware datetime
    pair for `field__gte=start, field__lt=end` filters, with end exclusive
    (end_date + 1 day, so end_date itself is included).

    Raises ValueError with a user-facing message on missing/invalid input or
    a range exceeding max_days - callers must check this BEFORE running any
    expensive query.
    """
    if not start_date or not end_date:
        raise ValueError("start_date and end_date are required.")

    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is None or end is None:
        raise ValueError("start_date and end_date must be valid dates in YYYY-MM-DD or DD-MM-YYYY format.")

    if end < start:
        raise ValueError("end_date must be greater than or equal to start_date.")

    if (end - start).days + 1 > max_days:
        raise ValueError(f"Date range cannot exceed {max_days} days.")

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, datetime.min.time()), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), datetime.min.time()), tz)

    return start_dt, end_dt


def _param(query_params, name):
    value = query_params.get(name)
    if value is None or str(value).strip().lower() in _ABSENT_VALUES:
        return None
    return str(value).strip()


def resolve_period(query_params, max_days=TRENDING_MAX_RANGE_DAYS):
    """
    Resolves the analytics window from either filter style:
    - month/year (older apps): used whenever month or year is sent - the
      existing get_month_range() path, unchanged (same validation/messages).
    - start_date/end_date (newer apps): used only when month/year are absent
      (or empty/"null"); validated by get_date_range() with max_days.
    With neither sent, falls through to month/year so the existing
    "month and year are required." error is preserved.

    Returns (start, end, period): tz-aware [start, end) datetimes, and the
    period keys for the response - month/year (integers; in range mode the
    month/year of start_date) plus start_date/end_date (YYYY-MM-DD, end
    inclusive).

    Raises ValueError with a user-facing message on invalid input, before any
    query runs.
    """
    month = _param(query_params, "month")
    year = _param(query_params, "year")
    start_date = _param(query_params, "start_date")
    end_date = _param(query_params, "end_date")

    if month is None and year is None and (start_date is not None or end_date is not None):
        start, end = get_date_range(start_date, end_date, max_days=max_days)
    else:
        start, end = get_month_range(month, year)

    first_day = start.date()
    last_day = (end - timedelta(days=1)).date()
    period = {
        "month": first_day.month,
        "year": first_day.year,
        "start_date": first_day.isoformat(),
        "end_date": last_day.isoformat(),
    }
    return start, end, period


def get_week_bucket(day):
    """
    Maps a day-of-month (1-31) to a calendar-day week bucket (1-5):
    1-7, 8-14, 15-21, 22-28, 29-31. Avoids assuming every month has exactly
    4 weeks, so activity in a 29/30/31-day month's tail isn't lost.
    """
    return min((day - 1) // 7 + 1, 5)


def days_in_month(month, year):
    return calendar.monthrange(year, month)[1]
