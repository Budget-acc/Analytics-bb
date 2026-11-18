"""
Block new or edited entries dated before the cutoff for archived shops.

A past-dated entry would recalculate an old day (update_daily_balance) whose
rows are already gone, so the daily balance chain would go wrong.
"""

from datetime import date, datetime

from django.utils import timezone

from .config import archive_cutoff, is_archived_shop

CLOSED_DATE_MESSAGE = "This date is closed. Please choose a newer date."


def closed_date_error(shop_id, when):
    """Return an error message if `when` (date or datetime) is before the cutoff, else None."""
    if when is None or not is_archived_shop(shop_id):
        return None
    cutoff = archive_cutoff()
    if isinstance(when, datetime):
        moment = when if timezone.is_aware(when) else timezone.make_aware(when)
        return CLOSED_DATE_MESSAGE if moment < cutoff else None
    if isinstance(when, date):
        return CLOSED_DATE_MESSAGE if when < timezone.localtime(cutoff).date() else None
    return None