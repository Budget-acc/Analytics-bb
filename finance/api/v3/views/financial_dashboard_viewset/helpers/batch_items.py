from collections import defaultdict


def _batch_booking_items(booking_ids):
    """{ booking_id: "Name A, Name B" } — product names for each booking."""
    if not booking_ids:
        return {}
    from booking_v2.models import BookedProductVariant
    rows = BookedProductVariant.objects.filter(
        booking_id__in=booking_ids, deleted__isnull=True
    ).values("booking_id", "product_name")
    grouped = defaultdict(list)
    for r in rows:
        if r["product_name"]:
            grouped[r["booking_id"]].append(r["product_name"])
    return {k: ", ".join(v) for k, v in grouped.items()}


def _batch_sale_items(sale_ids):
    """{ sale_id: "Name A, Name B" } — product names for each sale."""
    if not sale_ids:
        return {}
    from sales.models import SaleItem
    rows = SaleItem.objects.filter(sale_id__in=sale_ids).values(
        "sale_id", "product_variant__product__name"
    )
    grouped = defaultdict(list)
    for r in rows:
        name = r["product_variant__product__name"]
        if name:
            grouped[r["sale_id"]].append(name)
    return {k: ", ".join(v) for k, v in grouped.items()}


def _batch_tailor_items(order_ids):
    """{ order_id: "Name A, Name B" } — product names for each tailor order."""
    if not order_ids:
        return {}
    from tailored.models import TailorOrderItem
    rows = TailorOrderItem.objects.filter(order_id__in=order_ids).values(
        "order_id", "product_name"
    )
    grouped = defaultdict(list)
    for r in rows:
        if r["product_name"]:
            grouped[r["order_id"]].append(r["product_name"])
    return {k: ", ".join(v) for k, v in grouped.items()}


def _tailor_order_account_map(order_ids):
    """{ order_id: account_name } — first customer payment's account per tailor order."""
    if not order_ids:
        return {}
    from tailored.models import CustomerPayment
    rows = (
        CustomerPayment.objects.filter(order_id__in=order_ids)
        .order_by("order_id", "created_at")
        .values("order_id", "account__name")
    )
    result = {}
    for row in rows:
        oid = row["order_id"]
        if oid not in result:
            result[oid] = row["account__name"]
    return result
