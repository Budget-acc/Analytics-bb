from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Case, Count, F, IntegerField, Sum, Value, When
from django.db.models.functions import Coalesce

from booking_v2.models import BookingV2
from sales.models import Sale
from shop.models import Staff
from tailored.models import TailorOrder


def _as_amount(value):
    """Whole rupees as int; otherwise rounded to 2 decimal places."""
    value = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(value) if value == value.to_integral_value() else float(value)


def get_staff_performance(shop_id, start, end):
    """
    Staff performance for the given [start, end) window, using the same
    earnings rules as the staff earnings report (implemented independently
    here - no code is shared with any other API version):

    - Bookings: booking_status="completed" with completed_at in the window
      (cancelled deliveries are NOT excluded); earned = paid `advance_amount`.
    - Sales: sale_date in the window; earned = total_amount - discount per
      sale, or 0 when the discount covers the total.
    - Tailor Orders (Custom Work): status="completed", not converted into a
      booking, not deleted, completed_at in the window; earned = paid_amount,
      floored at 0 per staff member.

    earned_amount = bookings + sales + tailor orders; transaction_count =
    booking_count + sale_count + tailor_order_count. Results are ordered by
    earned_amount (then transaction_count), highest first.
    """
    booking_rows = (
        BookingV2.objects.filter(
            shop_id=shop_id,
            staff_id__isnull=False,
            booking_status="completed",
            completed_at__gte=start,
            completed_at__lt=end,
        )
        .values("staff_id")
        .annotate(
            count=Count("id"),
            earned=Sum(Coalesce(F("advance_amount"), Value(0))),
        )
    )

    sale_rows = (
        Sale.objects.filter(
            shop_id=shop_id,
            staff_id__isnull=False,
            sale_date__gte=start.date(),
            sale_date__lt=end.date(),
        )
        .values("staff_id")
        .annotate(
            count=Count("id"),
            earned=Sum(Case(
                When(total_amount__gt=F("discount"), then=F("total_amount") - F("discount")),
                default=Value(0),
                output_field=IntegerField(),
            )),
        )
    )

    tailor_rows = (
        TailorOrder.objects.filter(
            shop_id=shop_id,
            staff_id__isnull=False,
            status="completed",
            created_booking__isnull=True,
            deleted__isnull=True,
            completed_at__gte=start,
            completed_at__lt=end,
        )
        .values("staff_id")
        .annotate(count=Count("id"), earned=Sum("paid_amount"))
    )

    merged = {}

    def _accumulate(rows, count_key, floor_at_zero=False):
        for row in rows:
            staff_id = row["staff_id"]
            entry = merged.setdefault(staff_id, {
                "staff_id": staff_id,
                "booking_count": 0,
                "sale_count": 0,
                "tailor_order_count": 0,
                "earned_amount": Decimal("0"),
            })
            earned = Decimal(str(row["earned"] or 0))
            if floor_at_zero:
                earned = max(earned, Decimal("0"))
            entry[count_key] += row["count"]
            entry["earned_amount"] += earned

    _accumulate(booking_rows, "booking_count")
    _accumulate(sale_rows, "sale_count")
    _accumulate(tailor_rows, "tailor_order_count", floor_at_zero=True)

    staff_names = dict(
        Staff.objects.filter(id__in=merged.keys()).values_list("id", "name")
    )

    results = []
    for staff_id, entry in merged.items():
        entry["earned_amount"] = _as_amount(entry["earned_amount"])
        entry["staff_name"] = staff_names.get(staff_id, "")
        entry["transaction_count"] = (
            entry["booking_count"] + entry["sale_count"] + entry["tailor_order_count"]
        )
        results.append(entry)

    return sorted(
        results,
        key=lambda entry: (entry["earned_amount"], entry["transaction_count"]),
        reverse=True,
    )
