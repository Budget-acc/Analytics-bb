from collections import defaultdict

from django.db.models import Count, Sum

from booking_v2.models import BookedProductVariant
from sales.models import Sale, SaleItem
from services.models import Product, ProductVariant

from .common import active_booking_items

PRODUCT_ID_FIELD = "product_variant__product_id"
VARIANT_ID_FIELD = "product_variant_id"
VARIANT_NAME_FIELD = "product_variant__attribute"


def _has_field(model, name):
    return any(field.name == name for field in model._meta.get_fields())


# Sale/SaleItem are currently plain models: deleting a sale is a hard delete
# (the row is removed and its items cascade), so deleted sales never reach
# these queries. These flags add the soft-delete filter automatically should
# either model ever become soft-deletable.
_SALE_SOFT_DELETE = _has_field(Sale, "deleted")
_SALE_ITEM_SOFT_DELETE = _has_field(SaleItem, "deleted")


def _booked_items_qs(shop_id, start, end):
    # Soft-deleted and cancelled bookings are excluded (see active_booking_items).
    return active_booking_items(
        BookedProductVariant.objects.filter(
            shop_id=shop_id,
            booking__created_at__gte=start,
            booking__created_at__lt=end,
        )
    )


def _sale_items_qs(shop_id, start, end):
    qs = SaleItem.objects.filter(
        sale__shop_id=shop_id,
        sale__sale_date__gte=start.date(),
        sale__sale_date__lt=end.date(),
    )
    if _SALE_SOFT_DELETE:
        qs = qs.filter(sale__deleted__isnull=True)
    if _SALE_ITEM_SOFT_DELETE:
        qs = qs.filter(deleted__isnull=True)
    return qs


def _get_variant_breakdown(shop_id, start, end, product_ids):
    """
    Variant-level breakdown for the given products, using the same
    date/eligibility rules as the product-level counts. One grouped query per
    source, scoped to `product_ids`, plus one query listing every
    (non-deleted) variant of those products so variants without activity are
    included with zero values; returns {product_id: [variant, ...]}.
    """
    booking_rows = (
        _booked_items_qs(shop_id, start, end)
        .filter(product_variant__product_id__in=product_ids)
        .values(PRODUCT_ID_FIELD, VARIANT_ID_FIELD, VARIANT_NAME_FIELD)
        .annotate(count=Count("id"), amount=Sum("amount"))
    )
    sale_rows = (
        _sale_items_qs(shop_id, start, end)
        .filter(product_variant__product_id__in=product_ids)
        .values(PRODUCT_ID_FIELD, VARIANT_ID_FIELD, VARIANT_NAME_FIELD)
        .annotate(count=Count("id"), amount=Sum("subtotal"))
    )

    by_product = defaultdict(dict)

    def _accumulate(rows, amount_key):
        for row in rows:
            product_id = row[PRODUCT_ID_FIELD]
            variant_id = row[VARIANT_ID_FIELD]
            if product_id is None or variant_id is None:
                continue
            entry = by_product[product_id].setdefault(variant_id, {
                "variant_id": variant_id,
                "variant_name": row[VARIANT_NAME_FIELD],
                "count": 0,
                "booking_total_amount": 0,
                "sales_total_amount": 0,
            })
            entry["count"] += row["count"]
            entry[amount_key] += row["amount"] or 0

    _accumulate(booking_rows, "booking_total_amount")
    _accumulate(sale_rows, "sales_total_amount")

    # Every variant of each product, even with no activity (zero values).
    # Variants with activity keep their values - including any since
    # soft-deleted - so variant values still sum to the product totals.
    all_variants = ProductVariant.objects.filter(product_id__in=product_ids).values(
        "id", "product_id", "attribute"
    )
    for variant in all_variants:
        by_product[variant["product_id"]].setdefault(variant["id"], {
            "variant_id": variant["id"],
            "variant_name": variant["attribute"],
            "count": 0,
            "booking_total_amount": 0,
            "sales_total_amount": 0,
        })

    for variants in by_product.values():
        for entry in variants.values():
            entry["total_amount"] = entry["booking_total_amount"] + entry["sales_total_amount"]

    return {
        product_id: sorted(
            variants.values(),
            key=lambda v: (-v["count"], -v["total_amount"], v["variant_id"]),
        )
        for product_id, variants in by_product.items()
    }


def get_trending_products(shop_id, start, end, limit=None, offset=0, include_variants=False):
    """
    Trending products for the given [start, end) window - Bookings filtered
    by `created_at`, Sales filtered by `sale_date` (not `created_at` - the
    date the sale occurred, not when the record was created), combining
    Booking and Sale line items. Each booked/sold product-variant
    line item counts individually (NOT deduplicated by booking/sale), so two
    variants of the same product in one transaction contribute 2 to that
    product's count. Quantity is not factored in - one line item = one count.
    Ranking is by this combined line-item count (highest first); products
    with equal counts are ordered by total_amount (highest first), then
    product_id.

    Each result also carries a revenue breakdown, summed directly from
    item-level amounts with no discount recalculation:
    - booking_total_amount: sum of BookedProductVariant.amount for this product.
    - sales_total_amount: sum of SaleItem.subtotal for this product.
    - total_amount: booking_total_amount + sales_total_amount.

    Each result also carries service details:
    - model: Product.model.
    - service_id: the product's ShopOwnerService id (Product.shop_service_id).
    - main_service_name: MainServiceCategory.name via
      shop_service.general_service.main_category (None if unset).

    Soft-deleted and cancelled Bookings are excluded (see _booked_items_qs);
    deleted Sales are excluded too (see _sale_items_qs).

    When `include_variants` is true, each result also carries `variants`:
    every variant of that product - those with qualifying Booking/Sale
    activity first (by count, then total_amount), then variants with no
    activity at 0 - each as {variant_id, variant_name
    (ProductVariant.attribute), count, booking_total_amount,
    sales_total_amount, total_amount}, using the same window/eligibility and
    item-level amounts as the product totals (so they sum to the product's
    values). Off by default - costs 3 extra queries.

    Returns {"results": [...], "has_more": bool}. Reusable with a small
    `limit` (dashboard), a larger `limit`/`offset` (paginated "View More"),
    or `limit=None` to return every ranked product unsliced (`has_more` is
    always False in that case since nothing is held back).
    """
    booking_counts = (
        _booked_items_qs(shop_id, start, end)
        .values("product_variant__product_id")
        .annotate(count=Count("id"), amount=Sum("amount"))
    )

    sale_counts = (
        _sale_items_qs(shop_id, start, end)
        .values("product_variant__product_id")
        .annotate(count=Count("id"), amount=Sum("subtotal"))
    )

    # Booking and Sale amounts must stay attributed to their own source (not
    # summed together yet), so this isn't a plain single-field count merge -
    # accumulate count + both source amounts together per product.
    merged = {}
    for row in booking_counts:
        product_id = row["product_variant__product_id"]
        if product_id is None:
            continue
        entry = merged.setdefault(product_id, {"count": 0, "booking_amount": 0, "sales_amount": 0})
        entry["count"] += row["count"]
        entry["booking_amount"] += row["amount"] or 0

    for row in sale_counts:
        product_id = row["product_variant__product_id"]
        if product_id is None:
            continue
        entry = merged.setdefault(product_id, {"count": 0, "booking_amount": 0, "sales_amount": 0})
        entry["count"] += row["count"]
        entry["sales_amount"] += row["amount"] or 0

    # Drop products deleted/soft-deleted since the booking/sale was made BEFORE
    # ranking and slicing, so a deleted product never leaves a gap in a page
    # (the next-ranked product takes its place) and has_more stays accurate.
    live_product_ids = set(
        Product.objects.filter(id__in=list(merged)).values_list("id", flat=True)
    )
    merged = {pid: entry for pid, entry in merged.items() if pid in live_product_ids}

    # Rank by count (highest first); equal counts by total_amount (highest
    # first); product_id only breaks a tie on both, so the order is stable.
    ranked = sorted(
        merged.items(),
        key=lambda item: (
            -item[1]["count"],
            -(item[1]["booking_amount"] + item[1]["sales_amount"]),
            item[0],
        ),
    )
    if limit is None:
        page = ranked[offset:]
        has_more = False
    else:
        page = ranked[offset:offset + limit]
        has_more = len(ranked) > offset + limit

    product_ids = [product_id for product_id, _ in page]
    products_by_id = {
        product.id: product
        for product in Product.objects.filter(id__in=product_ids)
        .select_related("shop_service__general_service__main_category")
        .only(
            "id", "name", "description", "color", "category", "image", "thumbnail_image",
            "model", "shop_service_id",
            "shop_service__general_service__main_category__name",
        )
    }

    variants_by_product = (
        _get_variant_breakdown(shop_id, start, end, product_ids) if include_variants else {}
    )

    results = []
    for product_id, entry in page:
        product = products_by_id.get(product_id)
        if not product:
            # Deleted products are already filtered out before ranking; this
            # only guards a product deleted between the two queries.
            continue
        main_category = product.shop_service.general_service.main_category
        result_item = {
            "product_id": product.id,
            "name": product.name,
            "description": product.description,
            "color": product.color,
            "category": product.category,
            "image": product.image,
            "thumbnail_image": product.thumbnail_image,
            "model": product.model,
            "service_id": product.shop_service_id,
            "main_service_name": main_category.name if main_category else None,
            "count": entry["count"],
            "booking_total_amount": entry["booking_amount"],
            "sales_total_amount": entry["sales_amount"],
            "total_amount": entry["booking_amount"] + entry["sales_amount"],
        }
        if include_variants:
            result_item["variants"] = variants_by_product.get(product_id, [])
        results.append(result_item)

    return {"results": results, "has_more": has_more}
