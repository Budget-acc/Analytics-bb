from django.db.models import Count

from booking_v2.models import BookedProductVariant
from sales.models import SaleItem

from .common import merge_booking_sale_counts


def _get_trending_attribute_counts(shop_id, start, end, attribute_field, limit):
    """
    Shared core for "trending by product attribute" (color, category, ...).
    Reuses the same Booking/Sale created-date filtering as
    `get_trending_products`, just grouped by `attribute_field` instead of
    `product_variant__product_id`.
    """
    booking_rows = (
        BookedProductVariant.objects.filter(
            shop_id=shop_id,
            booking__created_at__gte=start,
            booking__created_at__lt=end,
        )
        .values(attribute_field)
        .annotate(count=Count("id"))
    )

    sale_rows = (
        SaleItem.objects.filter(
            sale__shop_id=shop_id,
            sale__created_at__gte=start,
            sale__created_at__lt=end,
        )
        .values(attribute_field)
        .annotate(count=Count("id"))
    )

    merged = merge_booking_sale_counts(booking_rows, sale_rows, attribute_field)
    return sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:limit]


def get_trending_colors(shop_id, start, end, limit=5):
    """
    Top product colors booked/sold in the given [start, end) created-date
    window, combining Booking and Sale line items (same counting rules as
    `get_trending_products` - one line item = one count). Products with a
    blank/null color are excluded. `limit` defaults to 5, i.e. at least the
    required top 3, without zero-padding when fewer distinct colors exist.
    """
    ranked = _get_trending_attribute_counts(
        shop_id, start, end, "product_variant__product__color", limit
    )
    return [{"color": value, "booking_count": count} for value, count in ranked]


def get_trending_categories(shop_id, start, end, limit=5):
    """
    Top product categories booked/sold in the given [start, end) created-date
    window - see `get_trending_colors` for the shared counting rules.
    """
    ranked = _get_trending_attribute_counts(
        shop_id, start, end, "product_variant__product__category", limit
    )
    return [{"category": value, "booking_count": count} for value, count in ranked]
