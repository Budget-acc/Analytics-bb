from datetime import datetime
from django.utils import timezone


def parse_custom_date(date_str):
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

def parse_date_string(date_str):
    formats = ["%d-%m-%Y", "%d-%m-%YT%H:%M:%S"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue


class InvalidDateFormat(Exception):
    """Custom exception for invalid date formats."""
    def __init__(self, date_str):
        self.date_str = date_str
        self.message = "Invalid date format."
        self.dev_message = f"Failed to parse date: {date_str}"
        super().__init__(self.dev_message)

def parse_datetime(date_str):
    """
    Parse flexible date/time strings like:
    - yyyy-mm-dd
    - dd-mm-yyyy
    - with or without time
    - with or without 'T' separator
    Returns a timezone-aware datetime object.
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    formats = [
        "%Y-%m-%dT%H:%M:%S",  # ISO (yyyy-mm-ddTHH:MM:SS)
        "%Y-%m-%d %H:%M:%S",  # yyyy-mm-dd HH:MM:SS
        "%Y-%m-%d %H:%M",     # yyyy-mm-dd HH:MM
        "%Y-%m-%d",           # yyyy-mm-dd

        "%d-%m-%YT%H:%M:%S",  # dd-mm-yyyyTHH:MM:SS
        "%d-%m-%YT%H:%M",     # dd-mm-yyyyTHH:MM
        "%d-%m-%Y %H:%M:%S",  # dd-mm-yyyy HH:MM:SS
        "%d-%m-%Y %H:%M",     # dd-mm-yyyy HH:MM
        "%d-%m-%Y",           # dd-mm-yyyy
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # If time is missing, default to 00:00:00
            if 'H' not in fmt:
                dt = dt.replace(hour=0, minute=0, second=0)
            return timezone.make_aware(dt)
        except ValueError:
            continue

    # If none of the formats matched, raise custom exception
    raise InvalidDateFormat(date_str)
