from collections import defaultdict
from decimal import Decimal

from django.db.models import Prefetch
from django.utils.timezone import localtime


def _bookings_dates(shop_id, client):
    from booking_v2.models import BookingV2
    qs = (
        BookingV2.objects.filter(shop_id=shop_id, deleted__isnull=True)
        .exclude(delivery_status="cancelled")
    )
    if client:
        qs = qs.filter(client_identity=client)
    return set(qs.values_list("created_at__date", flat=True).distinct())


def _fetch_bookings(shop_id, client, page_dates):
    from booking_v2.models import BookingV2, BookedProductVariant

    qs = (
        BookingV2.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            created_at__date__in=page_dates,
        )
        .exclude(delivery_status="cancelled")
        .select_related("client_identity")
        .prefetch_related(
            Prefetch(
                "booked_items",
                queryset=BookedProductVariant.objects.filter(deleted__isnull=True),
                to_attr="_booked_items",
            )
        )
        .order_by("-created_at")
    )
    if client:
        qs = qs.filter(client_identity=client)

    bookings = list(qs)
    account_map = _booking_account_map([b.id for b in bookings])

    by_date = defaultdict(list)
    for booking in bookings:
        date_obj = localtime(booking.created_at).date()
        items_str = ", ".join(
            i.product_name
            for i in getattr(booking, "_booked_items", [])
            if i.product_name
        )
        account_name = account_map.get(booking.id)
        by_date[date_obj].append({
            "booking_id": booking.id,
            "invoice_id": booking.shop_booking_id,
            "client_name": (
                booking.client_identity.client_name
                if booking.client_identity else None
            ),
            "account_name": account_name,
            "total_amount": (booking.total_amount or 0) - (booking.discount_amount or 0),
            "paid_amount": booking.advance_amount or 0,
            "items": items_str,
            "created_at": localtime(booking.created_at).strftime("%d-%m-%Y %H:%M:%S"),
        })

    result = {}
    for date_obj, items in by_date.items():
        acc_totals = defaultdict(lambda: Decimal(0))
        total = Decimal(0)
        for item in items:
            acc = item["account_name"] or "Unknown"
            acc_totals[acc] += item["paid_amount"]
            total += item["paid_amount"]
        result[date_obj] = {
            "accounts": [
                {"name": name, "amount": str(amount)}
                for name, amount in acc_totals.items()
                if amount > 0
            ],
            "total": total,
            "items": items,
        }
    return result


def _booking_account_map(booking_ids):
    """{ booking_id: account_name } — first payment's account per booking."""
    if not booking_ids:
        return {}
    from booking_v2.models import PaymentDetailsV2
    rows = (
        PaymentDetailsV2.objects.filter(booking_id__in=booking_ids)
        .order_by("booking_id", "created_at")
        .values("booking_id", "account__name")
    )
    result = {}
    for row in rows:
        bid = row["booking_id"]
        if bid not in result:
            result[bid] = row["account__name"]
    return result
