import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("api_requests")


def safe_decimal(value, default=Decimal('0')):
    """Safely convert value to Decimal with error handling."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError) as e:
        logger.warning(f"Invalid decimal conversion for value '{value}': {e}")
        return default


def parse_date(date_str):
    if not date_str or not isinstance(date_str, str):
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None
