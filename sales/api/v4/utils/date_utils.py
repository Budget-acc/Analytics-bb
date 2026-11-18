from datetime import datetime

from django.utils import timezone
import pytz

IST = pytz.timezone("Asia/Kolkata")


def ensure_ist(dt):
    """Convert datetime to IST timezone"""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt.astimezone(IST)


def parse_custom_date(date_str):
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None
