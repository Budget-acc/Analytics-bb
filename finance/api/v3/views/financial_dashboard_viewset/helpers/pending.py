from collections import defaultdict
from decimal import Decimal

from django.db.models import F, Value, Sum, DecimalField
from django.db.models.functions import Coalesce
from django.utils.timezone import localtime

from .batch_items import _batch_booking_items, _batch_tailor_items


def _pending_dates(shop_id, client):
    from booking_v2.models import BookingV2
    from tailored.models import TailorOrder

    booking_qs = (
        BookingV2.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            return_date__isnull=False,
            total_amount__gt=0,
        )
        .exclude(delivery_status="cancelled")
        .annotate(
            remaining=Coalesce(F("total_amount"), Value(0))
            - Coalesce(F("advance_amount"), Value(0))
            - Coalesce(F("discount_amount"), Value(0))
        )
        .filter(remaining__gt=0)
    )
    if client:
        booking_qs = booking_qs.filter(client_identity=client)

    tailor_qs = (
        TailorOrder.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            customer_order_type="rental",
            created_booking__isnull=True,
            return_date__isnull=False,
        )
        .annotate(
            remaining=Coalesce(F("customer_total_charge"), Value(0, output_field=DecimalField()))
            - Coalesce(F("paid_amount"), Value(0, output_field=DecimalField()))
            - Coalesce(F("discount_amount"), Value(0, output_field=DecimalField()))
        )
        .filter(remaining__gt=0)
    )
    if client:
        tailor_qs = tailor_qs.filter(client=client)

    return (
        set(booking_qs.values_list("return_date__date", flat=True).distinct())
        | set(tailor_qs.values_list("return_date__date", flat=True).distinct())
    )


def _fetch_pending(shop_id, client, page_dates):
    from booking_v2.models import BookingV2
    from tailored.models import TailorOrder

    booking_qs = (
        BookingV2.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            return_date__date__in=page_dates,
        )
        .exclude(delivery_status="cancelled")
        .select_related("client_identity")
        .order_by("return_date")
    )
    if client:
        booking_qs = booking_qs.filter(client_identity=client)

    bookings = list(booking_qs)
    booking_items_map = _batch_booking_items([b.id for b in bookings])

    tailor_qs = (
        TailorOrder.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            customer_order_type="rental",
            created_booking__isnull=True,
            return_date__date__in=page_dates,
        )
        .select_related("client")
        .order_by("return_date")
    )
    if client:
        tailor_qs = tailor_qs.filter(client=client)

    tailor_orders = list(tailor_qs)
    tailor_items_map = _batch_tailor_items([o.id for o in tailor_orders])

    by_date = defaultdict(list)

    for booking in bookings:
        remaining = (
            (booking.total_amount or 0)
            - (booking.advance_amount or 0)
            - (booking.discount_amount or 0)
        )
        if remaining <= 0:
            continue
        date_obj = localtime(booking.return_date).date()
        by_date[date_obj].append({
            "type": "booking",
            "reference_id": booking.id,
            "invoice_id": booking.shop_booking_id,
            "client_name": (
                booking.client_identity.client_name
                if booking.client_identity else None
            ),
            "remaining_amount": remaining,
            "return_date": localtime(booking.return_date).strftime("%d-%m-%Y"),
            "items": booking_items_map.get(booking.id, ""),
        })

    for order in tailor_orders:
        remaining = float(
            (order.customer_total_charge or 0)
            - (order.paid_amount or 0)
            - (order.discount_amount or 0)
        )
        if remaining <= 0:
            continue
        date_obj = localtime(order.return_date).date()
        by_date[date_obj].append({
            "type": "tailor_order",
            "reference_id": order.id,
            "invoice_id": order.shop_tailor_id,
            "client_name": order.client.client_name if order.client else None,
            "remaining_amount": remaining,
            "return_date": localtime(order.return_date).strftime("%d-%m-%Y"),
            "items": tailor_items_map.get(order.id, ""),
        })

    result = {}
    for date_obj, items in by_date.items():
        result[date_obj] = {
            "total_pending": sum(i["remaining_amount"] for i in items),
            "items": sorted(items, key=lambda x: x["remaining_amount"], reverse=True),
        }
    return result


def _total_pending_amount(shop_id, client):
    """Grand total pending across ALL dates — not scoped to the current page."""
    from booking_v2.models import BookingV2
    from tailored.models import TailorOrder

    booking_qs = (
        BookingV2.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            total_amount__gt=0,
            return_date__isnull=False,
        )
        .exclude(delivery_status="cancelled")
        .annotate(
            remaining=Coalesce(F("total_amount"), Value(0))
            - Coalesce(F("advance_amount"), Value(0))
            - Coalesce(F("discount_amount"), Value(0))
        )
        .filter(remaining__gt=0)
    )
    if client:
        booking_qs = booking_qs.filter(client_identity=client)
    booking_total = booking_qs.aggregate(total=Sum("remaining"))["total"] or 0

    tailor_qs = (
        TailorOrder.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            customer_order_type="rental",
            created_booking__isnull=True,
        )
        .annotate(
            remaining=Coalesce(F("customer_total_charge"), Value(0, output_field=DecimalField()))
            - Coalesce(F("paid_amount"), Value(0, output_field=DecimalField()))
            - Coalesce(F("discount_amount"), Value(0, output_field=DecimalField()))
        )
        .filter(remaining__gt=0)
    )
    if client:
        tailor_qs = tailor_qs.filter(client=client)
    tailor_total = tailor_qs.aggregate(total=Sum("remaining"))["total"] or 0

    return (booking_total or Decimal(0)) + (tailor_total or Decimal(0))
