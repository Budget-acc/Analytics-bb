from decimal import Decimal

from django.db.models import CharField, DateField, F, Sum, Value
from django.db.models.functions import Cast, Coalesce

from booking_v2.models import BookingV2
from sales.models import Sale
from tailored.models import TailorOrder

# Unified row shape produced by every branch below, so the three querysets
# stay union()-compatible: same field names, same order, same output types.
_ROW_FIELDS = ("reference_id", "date", "order_id", "type", "taxable_amount", "tax_rate", "tax_amount")


def _bookings_base_qs(shop_id, start_date, end_date):
    qs = BookingV2.objects.filter(
        shop_id=shop_id,
        deleted__isnull=True,
        is_tax_applied=True,
        booking_status="completed",
        completed_at__isnull=False,
    ).exclude(delivery_status="cancelled")
    if start_date:
        qs = qs.filter(completed_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(completed_at__date__lte=end_date)
    return qs


def _sales_base_qs(shop_id, start_date, end_date):
    qs = Sale.objects.filter(shop_id=shop_id, is_tax_applied=True)
    if start_date:
        qs = qs.filter(sale_date__gte=start_date)
    if end_date:
        qs = qs.filter(sale_date__lte=end_date)
    return qs


def _tailor_base_qs(shop_id, start_date, end_date):
    qs = TailorOrder.objects.filter(
        shop_id=shop_id,
        deleted__isnull=True,
        is_tax_applied=True,
        status="completed",
        completed_at__isnull=False,
    )
    if start_date:
        qs = qs.filter(completed_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(completed_at__date__lte=end_date)
    return qs


def _bookings_qs(shop_id, start_date, end_date):
    return _bookings_base_qs(shop_id, start_date, end_date).annotate(
        reference_id=F("id"),
        date=Cast("completed_at", output_field=DateField()),
        order_id=F("shop_booking_id"),
        type=Value("bookings", output_field=CharField()),
    ).values(*_ROW_FIELDS)


def _sales_qs(shop_id, start_date, end_date):
    return _sales_base_qs(shop_id, start_date, end_date).annotate(
        reference_id=F("id"),
        date=F("sale_date"),
        order_id=F("shop_sale_id"),
        type=Value("sales", output_field=CharField()),
    ).values(*_ROW_FIELDS)


def _tailor_qs(shop_id, start_date, end_date):
    return _tailor_base_qs(shop_id, start_date, end_date).annotate(
        reference_id=F("id"),
        date=Cast("completed_at", output_field=DateField()),
        order_id=F("shop_tailor_id"),
        type=Value("tailor_orders", output_field=CharField()),
    ).values(*_ROW_FIELDS)


def build_tax_report_queryset(shop_id, start_date, end_date):
    """
    Consolidated, tax-applicable rows from Bookings, Sales and Tailor Orders
    for a single shop, merged via union() and ordered by date descending.

    Only scalar snapshot fields are selected (no select_related/prefetch_related
    needed) so this stays cheap even for shops with a large history.
    """
    combined = (
        _bookings_qs(shop_id, start_date, end_date)
        .union(_sales_qs(shop_id, start_date, end_date))
        .union(_tailor_qs(shop_id, start_date, end_date))
    )
    return combined.order_by("-date")


def get_tax_report_summary(shop_id, start_date, end_date):
    """
    Aggregated totals across ALL matching rows for the given filter (not just
    the current page) — dynamically reflects start_date/end_date when given,
    or the shop's all-time totals when both are omitted.

    aggregate() cannot be called on a union()-combined queryset, so each
    module's base filtered queryset is aggregated independently and the
    partial sums are combined in Python.
    """
    zero = Decimal("0")
    base_querysets = (
        _bookings_base_qs(shop_id, start_date, end_date),
        _sales_base_qs(shop_id, start_date, end_date),
        _tailor_base_qs(shop_id, start_date, end_date),
    )

    total_taxable = zero
    total_tax = zero
    for base_qs in base_querysets:
        totals = base_qs.aggregate(
            taxable=Coalesce(Sum("taxable_amount"), zero),
            tax=Coalesce(Sum("tax_amount"), zero),
        )
        total_taxable += totals["taxable"]
        total_tax += totals["tax"]

    # Round CGST first, then derive SGST as the remainder so the two always
    # sum exactly to total_tax_amount - halving both independently and
    # rounding each to 2dp can drift by a paisa on odd-cent totals (e.g.
    # 99.99 -> 49.995 + 49.995, each rounds to 50.00).
    total_cgst = (total_tax / 2).quantize(Decimal("0.01"))
    total_sgst = total_tax - total_cgst

    return {
        "total_taxable_amount": total_taxable,
        "total_tax_amount": total_tax,
        "total_cgst": total_cgst,
        "total_sgst": total_sgst,
    }
