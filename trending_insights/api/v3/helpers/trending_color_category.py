import string

from django.db.models import Count, F, Value
from django.db.models.functions import Coalesce, Lower, NullIf, Trim

from booking_v2.models import BookedProductVariant
from sales.models import SaleItem

from .common import active_booking_items, merge_grouped_counts

COLOR_FIELD = "product_variant__product__color"
CATEGORY_FIELD = "product_variant__product__category"

UNCATEGORIZED = "Uncategorized"

TOP_LIMIT = 5


def _normalized(field):
    """Case-insensitive, trimmed grouping key: "Red", "RED " and "rED" all
    become "red". Null, blank or whitespace-only -> "uncategorized"."""
    return Coalesce(
        NullIf(Lower(Trim(F(field))), Value("")),
        Value(UNCATEGORIZED.lower()),
    )


def _display_label(key):
    """Title Case label for a normalized key ("jodhpuri suit" -> "Jodhpuri Suit",
    "uncategorized" -> "Uncategorized"). capwords avoids str.title()'s
    "Men'S" quirk."""
    return string.capwords(key)


def _grouped_color_category_counts(shop_id, start, end):
    """
    Single Booking-side + single Sale-side query, each grouped by
    (color, category) together - one DB aggregation per source, reused for
    both the color and category breakdowns (no separate query per axis).
    Values are grouped case-insensitively with surrounding spaces ignored,
    and blank/null color or category is normalized to "Uncategorized" - all
    at the DB level, so Python never branches on missing values. Keys are
    returned as Title Case display labels.
    """
    # Soft-deleted and cancelled bookings are excluded (see active_booking_items).
    booking_rows = (
        active_booking_items(
            BookedProductVariant.objects.filter(
                shop_id=shop_id,
                booking__created_at__gte=start,
                booking__created_at__lt=end,
            )
        )
        .annotate(
            color_value=_normalized(COLOR_FIELD),
            category_value=_normalized(CATEGORY_FIELD),
        )
        .values("color_value", "category_value")
        .annotate(count=Count("id"))
    )

    sale_rows = (
        SaleItem.objects.filter(
            sale__shop_id=shop_id,
            sale__created_at__gte=start,
            sale__created_at__lt=end,
        )
        .annotate(
            color_value=_normalized(COLOR_FIELD),
            category_value=_normalized(CATEGORY_FIELD),
        )
        .values("color_value", "category_value")
        .annotate(count=Count("id"))
    )

    merged = merge_grouped_counts(booking_rows, sale_rows, ("color_value", "category_value"))

    # Accumulate (not overwrite) in case two keys collapse to the same label,
    # e.g. ones differing only in inner spacing.
    labelled = {}
    for (color, category), count in merged.items():
        key = (_display_label(color), _display_label(category))
        labelled[key] = labelled.get(key, 0) + count
    return labelled


def _pick_top_n(totals, limit=TOP_LIMIT):
    """Picks up to `limit` highest-count keys (count descending), excluding
    the "Uncategorized" fallback - a missing color/category isn't a real
    trend. The sort is stable, so ties keep first-encountered order and the
    first entry is the same key a plain max() would have chosen. Returns an
    empty list if nothing eligible exists."""
    eligible = [(key, count) for key, count in totals.items() if key != UNCATEGORIZED]
    eligible.sort(key=lambda item: item[1], reverse=True)
    return [key for key, _ in eligible[:limit]]


def _build_top_entries(totals, breakdown_by_key, key_name, breakdown_key, breakdown_item_key):
    """Builds the top entries for one axis: each is {key_name: value,
    "booking_count": total, breakdown_key: [{breakdown_item_key: other,
    "booking_count": n}, ...]} with the breakdown sorted by count descending."""
    entries = []
    for key in _pick_top_n(totals):
        breakdown = sorted(
            (
                {breakdown_item_key: other, "booking_count": count}
                for other, count in breakdown_by_key[key].items()
            ),
            key=lambda item: item["booking_count"],
            reverse=True,
        )
        entries.append({
            key_name: key,
            "booking_count": totals[key],
            breakdown_key: breakdown,
        })
    return entries


def get_top_trending_color_category(shop_id, start, end, section=None):
    """
    Top trending Colors and/or Categories for the given [start, end) created-
    date window, combining Booking and Sale line items (same counting rules
    as get_trending_products - one line item = one count).

    section: None (both), "color", or "category" - restricts which
    top-level key(s) are computed/returned.

    Returns a dict with "top_trending_color" and/or "top_trending_category"
    keys. Each value is a list of up to TOP_LIMIT entries (empty if no
    eligible data that month), ordered by booking_count descending:
    {"color"/"category": ..., "booking_count": ..., "category_breakdown"/
    "color_breakdown": [...]}, where the breakdown's booking_counts always
    sum to exactly the parent's booking_count (including any Uncategorized
    contribution).
    """
    grouped = _grouped_color_category_counts(shop_id, start, end)

    color_totals, category_by_color = {}, {}
    category_totals, color_by_category = {}, {}

    for (color, category), count in grouped.items():
        color_totals[color] = color_totals.get(color, 0) + count
        category_by_color.setdefault(color, {})
        category_by_color[color][category] = category_by_color[color].get(category, 0) + count

        category_totals[category] = category_totals.get(category, 0) + count
        color_by_category.setdefault(category, {})
        color_by_category[category][color] = color_by_category[category].get(color, 0) + count

    result = {}

    if section in (None, "color"):
        result["top_trending_color"] = _build_top_entries(
            color_totals, category_by_color, "color", "category_breakdown", "category"
        )

    if section in (None, "category"):
        result["top_trending_category"] = _build_top_entries(
            category_totals, color_by_category, "category", "color_breakdown", "color"
        )

    return result
