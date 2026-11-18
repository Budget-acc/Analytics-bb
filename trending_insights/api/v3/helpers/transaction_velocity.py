from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate

from booking_v2.models import BookingV2
from sales.models import Sale

from .common import CANCELLED_DELIVERY_STATUS
from .date_utils import get_week_bucket


def get_transaction_velocity(shop_id, start, end):
    """
    Weekly booking/sale counts for the given [start, end) window - Bookings
    grouped by `created_at`, Sales grouped by `sale_date` (not `created_at` -
    the date the sale occurred, not when the record was created), bucketed
    by calendar-day ranges within the month (1-7, 8-14, 15-21, 22-28, 29-31)
    rather than a fixed 4-week assumption, so a 29/30/31-day month doesn't
    lose its tail days. Cancelled bookings (delivery_status="cancelled") are
    excluded; BookingV2.objects already hides soft-deleted bookings.

    When the window spans more than one calendar month (date-range requests
    only), `weeks` is empty and `months` holds one entry per calendar month
    instead; otherwise `months` is empty.
    """
    booking_days = (
        BookingV2.objects.filter(shop_id=shop_id, created_at__gte=start, created_at__lt=end)
        .exclude(delivery_status=CANCELLED_DELIVERY_STATUS)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )

    sale_days = (
        Sale.objects.filter(shop_id=shop_id, sale_date__gte=start.date(), sale_date__lt=end.date())
        .values("sale_date")
        .annotate(count=Count("id"))
    )

    first_day = start.date()
    last_day = (end - timedelta(days=1)).date()

    # Same calendar month/year -> the existing weekly breakdown (unchanged for
    # month/year requests). A range spanning several calendar months can't use
    # day-of-month buckets (3 Aug and 3 Sep would share "week 1"), so it gets
    # an empty `weeks` plus a per-month breakdown in `months` instead.
    if (first_day.year, first_day.month) == (last_day.year, last_day.month):
        weeks = {i: {"booking_count": 0, "sale_count": 0} for i in range(1, 6)}

        for row in booking_days:
            week = get_week_bucket(row["day"].day)
            weeks[week]["booking_count"] += row["count"]

        for row in sale_days:
            week = get_week_bucket(row["sale_date"].day)
            weeks[week]["sale_count"] += row["count"]

        return {
            "weeks": [
                {
                    "week": week,
                    "booking_count": data["booking_count"],
                    "sale_count": data["sale_count"],
                    "total_count": data["booking_count"] + data["sale_count"],
                }
                for week, data in sorted(weeks.items())
            ],
            "months": [],
        }

    # Every calendar month the range touches, oldest first (zero-activity
    # months included), each counting only the days inside the range.
    months = {}
    year, month = first_day.year, first_day.month
    while (year, month) <= (last_day.year, last_day.month):
        months[(year, month)] = {"booking_count": 0, "sale_count": 0}
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    for row in booking_days:
        months[(row["day"].year, row["day"].month)]["booking_count"] += row["count"]

    for row in sale_days:
        months[(row["sale_date"].year, row["sale_date"].month)]["sale_count"] += row["count"]

    return {
        "weeks": [],
        "months": [
            {
                "month": month,
                "year": year,
                "booking_count": data["booking_count"],
                "sale_count": data["sale_count"],
                "total_count": data["booking_count"] + data["sale_count"],
            }
            for (year, month), data in months.items()
        ],
    }
