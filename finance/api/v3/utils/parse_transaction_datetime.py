from datetime import datetime

from django.utils import timezone

_DATETIME_FORMATS = ['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S']
_DATE_FORMATS = ['%Y-%m-%d', '%d-%m-%Y']


def parse_transaction_datetime(value):
    """
    Parses transaction_date for Investment/Withdrawal. Accepts a full
    datetime (YYYY-MM-DDTHH:MM:SS or DD-MM-YYYYTHH:MM:SS) and uses it as-is,
    or a bare date (YYYY-MM-DD or DD-MM-YYYY) and combines it with the
    current server time. Returns an aware datetime, or None if unparseable.
    """
    if not value:
        return None

    for fmt in _DATETIME_FORMATS:
        try:
            naive = datetime.strptime(value, fmt)
            return timezone.make_aware(naive)
        except ValueError:
            continue

    for fmt in _DATE_FORMATS:
        try:
            date_part = datetime.strptime(value, fmt).date()
            now = timezone.localtime()
            return timezone.make_aware(datetime.combine(date_part, now.time()))
        except ValueError:
            continue

    return None
