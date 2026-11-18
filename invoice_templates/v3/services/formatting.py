"""Output formatting shared by the invoice template APIs."""
from django.utils import timezone

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_datetime(value):
    """Aware datetime -> 'YYYY-MM-DD HH:MM:SS' in the project time zone (Asia/Kolkata); None stays None."""
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime(DATETIME_FORMAT)
