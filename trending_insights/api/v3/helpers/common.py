from booking_v2.models import BookedProductVariant
from sales.models import SaleItem

CANCELLED_DELIVERY_STATUS = "cancelled"


def active_booking_items(items_qs):
    """
    Restrict a BookedProductVariant queryset to items of bookings that are
    neither soft-deleted nor cancelled (delivery_status="cancelled"). Shared
    by every booking-based analytics query so the rule lives in one place.

    BookedProductVariant.objects hides soft-deleted items, but soft delete is
    not applied across joins - soft-deleted bookings are excluded explicitly.
    `.exclude()` keeps bookings whose delivery_status is null.
    """
    return items_qs.filter(booking__deleted__isnull=True).exclude(
        booking__delivery_status=CANCELLED_DELIVERY_STATUS
    )


def _completed_booked_items_qs(shop_id, start, end):
    """
    Booked product-variant line items for completed, non-cancelled,
    non-deleted bookings in the given [start, end) completed-date window.
    Shared by every service/product revenue helper.
    """
    return active_booking_items(
        BookedProductVariant.objects.filter(
            shop_id=shop_id,
            booking__booking_status="completed",
            booking__completed_at__gte=start,
            booking__completed_at__lt=end,
        )
    )


def _eligible_sale_items_qs(shop_id, start, end):
    """
    Sale item line items for sales occurring in the given [start, end)
    window, filtered by `sale_date` (the date the sale occurred) - NOT
    `created_at` (when the record was created). Sales have no separate
    completion status, so all are eligible. Shared by every service/product
    revenue helper.
    """
    return SaleItem.objects.filter(
        sale__shop_id=shop_id,
        sale__sale_date__gte=start.date(),
        sale__sale_date__lt=end.date(),
    )


def merge_booking_sale_counts(booking_rows, sale_rows, value_field):
    """
    Merges two `.values(value_field).annotate(count=Count(...))` querysets
    (one from Bookings, one from Sales) into a single {value: total_count}
    dict, skipping null/blank values. Shared by every "trending X" helper so
    the Booking+Sale combine logic lives in exactly one place.
    """
    merged = {}
    for rows in (booking_rows, sale_rows):
        for row in rows:
            value = row[value_field]
            if not value:
                continue
            merged[value] = merged.get(value, 0) + row["count"]
    return merged


def merge_grouped_counts(booking_rows, sale_rows, key_fields):
    """
    Tuple-keyed generalization of `merge_booking_sale_counts`: merges two
    `.values(*key_fields).annotate(count=Count(...))` querysets into a single
    {(value1, value2, ...): total_count} dict. Unlike the single-field
    version, values are NOT skipped when falsy - callers that need a
    "Uncategorized" fallback normalize that at the DB level (e.g. via
    Coalesce/NullIf) before grouping, so every row here is already valid.
    """
    merged = {}
    for rows in (booking_rows, sale_rows):
        for row in rows:
            key = tuple(row[field] for field in key_fields)
            merged[key] = merged.get(key, 0) + row["count"]
    return merged
