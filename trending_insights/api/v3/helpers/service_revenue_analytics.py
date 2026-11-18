from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Case, Count, DecimalField, F, Sum, Value, When

from booking_v2.models import BookingAdditionalCharge
from services.models import ShopOwnerService
from tailored.models import TailorOrder

from .common import _completed_booked_items_qs, _eligible_sale_items_qs

SERVICE_ID_FIELD = "product_variant__product__shop_service_id"
SERVICE_NAME_FIELD = "product_variant__product__shop_service__general_service__name"
PRODUCT_ID_FIELD = "product_variant__product_id"
PRODUCT_NAME_FIELD = "product_variant__product__name"

OTHERS_LABEL = "Others"
CUSTOM_WORK_SERVICE_NAME = "Custom Work"

_ZERO = Decimal("0")
_TWO_PLACES = Decimal("0.01")
_TAX_FIELDS = ("is_tax_applied", "tax_calculation_type", "tax_rate", "taxable_components")


def _dec(value):
    return Decimal(str(value or 0))


def _round2(value):
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _as_amount(value):
    """Whole rupees as int; otherwise rounded to 2 decimal places."""
    value = _round2(_dec(value))
    return int(value) if value == value.to_integral_value() else float(value)


def _exclusive_product_tax_rate(txn):
    """
    Exclusive tax rate (as a fraction) applicable to product amounts, from
    the transaction's own stored tax snapshot; 0 when there is no tax, the
    tax is Inclusive (already inside the amounts), or products are not a
    taxable component. Records taxed before taxable_components existed have
    it empty - tax was charged on their products, so they count as taxable.
    """
    if not txn["is_tax_applied"] or txn["tax_calculation_type"] != "exclusive":
        return _ZERO
    components = set(txn["taxable_components"] or [])
    if components and not ({"entire_invoice", "product_total"} & components):
        return _ZERO
    return _dec(txn["tax_rate"]) / Decimal("100")


def _net_product_lines(rows, prefix, discount_field, other_amount_by_txn=None):
    """
    Net amount per (transaction, service, product) line, from DB rows grouped
    by those keys. Per transaction:
      1. its discount is shared by price ratio - for Bookings first between
         products and additional charges (`other_amount_by_txn`), as the
         stored booking tax does; then across product lines by amount;
      2. Exclusive tax (stored rate) is added on the discounted amount;
         Inclusive tax is already inside the amount and is not added.
    Each line's final amount is rounded to 2 dp. Yields
    (txn_id, service_id, service_name, product_id, product_name, final).
    """
    other_amount_by_txn = other_amount_by_txn or {}
    txns = {}
    for row in rows:
        txn = txns.setdefault(row[f"{prefix}_id"], {
            "discount": _dec(row[discount_field]),
            **{field: row[f"{prefix}__{field}"] for field in _TAX_FIELDS},
            "lines": [],
        })
        txn["lines"].append({
            "service_id": row[SERVICE_ID_FIELD],
            "service_name": row[SERVICE_NAME_FIELD],
            "product_id": row[PRODUCT_ID_FIELD],
            "product_name": row[PRODUCT_NAME_FIELD],
            "amount": _dec(row["amount"]),
        })

    for txn_id, txn in txns.items():
        product_total = sum((line["amount"] for line in txn["lines"]), _ZERO)
        combined_total = product_total + _dec(other_amount_by_txn.get(txn_id))
        discount = min(txn["discount"], combined_total)
        product_discount = discount * product_total / combined_total if combined_total > 0 else _ZERO
        tax_rate = _exclusive_product_tax_rate(txn)

        for line in txn["lines"]:
            line_discount = (
                product_discount * line["amount"] / product_total if product_total > 0 else _ZERO
            )
            discounted = max(line["amount"] - line_discount, _ZERO)
            final = _round2(discounted + discounted * tax_rate)
            yield (txn_id, line["service_id"], line["service_name"],
                   line["product_id"], line["product_name"], final)


def _custom_work_entry(shop_id, start, end):
    """
    Single "Custom Work" service entry for completed Tailor Orders in the
    given [start, end) completed_at window, excluding orders converted into a
    booking (created_booking set). Per order revenue is customer_total_charge
    (item + additional charges, Exclusive tax included, pre-discount) minus
    discount_amount, never below 0. Not product-based, so product_breakdown
    is empty. Returns None when there are no eligible orders.
    """
    agg = TailorOrder.objects.filter(
        shop_id=shop_id,
        status="completed",
        created_booking__isnull=True,
        deleted__isnull=True,
        completed_at__gte=start,
        completed_at__lt=end,
    ).aggregate(
        count=Count("id"),
        revenue=Sum(Case(
            When(
                customer_total_charge__gt=F("discount_amount"),
                then=F("customer_total_charge") - F("discount_amount"),
            ),
            default=Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )),
    )
    if not agg["count"]:
        return None

    revenue = Decimal(str(agg["revenue"] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    revenue = int(revenue) if revenue == revenue.to_integral_value() else float(revenue)
    return {
        "service_id": None,
        "service_name": CUSTOM_WORK_SERVICE_NAME,
        "booking_count": 0,
        "sales_count": 0,
        "total_transaction_count": agg["count"],
        "booking_revenue": 0,
        "sales_revenue": 0,
        "total_revenue": revenue,
        "product_breakdown": [],
    }


def _grouped_lines_qs(items_qs, prefix, amount_field, discount_field):
    """One DB-aggregated row per (transaction, service, product)."""
    return (
        items_qs.order_by()
        .values(
            f"{prefix}_id", discount_field,
            *(f"{prefix}__{field}" for field in _TAX_FIELDS),
            SERVICE_ID_FIELD, SERVICE_NAME_FIELD, PRODUCT_ID_FIELD, PRODUCT_NAME_FIELD,
        )
        .annotate(amount=Sum(amount_field))
    )


def _service_product_lines(shop_id, start, end):
    """
    Net (discount- and tax-adjusted) product lines for eligible Bookings and
    Sales - one grouped query per source plus one for Booking additional-
    charge totals. Returns (booking_lines, sale_lines).
    """
    booked_items = _completed_booked_items_qs(shop_id, start, end)
    additional_by_booking = dict(
        BookingAdditionalCharge.objects
        .filter(booking_id__in=booked_items.order_by().values("booking_id"))
        .values("booking_id")
        .annotate(total=Sum("amount"))
        .values_list("booking_id", "total")
    )
    booking_lines = _net_product_lines(
        _grouped_lines_qs(booked_items, "booking", "amount", "booking__discount_amount"),
        "booking", "booking__discount_amount", additional_by_booking,
    )
    sale_lines = _net_product_lines(
        _grouped_lines_qs(_eligible_sale_items_qs(shop_id, start, end), "sale", "subtotal", "sale__discount"),
        "sale", "sale__discount",
    )
    return booking_lines, sale_lines


def _percentage(amount, total):
    if not total:
        return 0
    return round(amount / total * 100)


def get_service_revenue_analytics(shop_id, start, end, top_n=2):
    """
    Per-service revenue - the single source of the Service Revenue
    calculation; the dashboard's `get_service_revenue` is derived from this -
    with Booking/Sales kept separate (booking_count,
    sales_count, booking_revenue, sales_revenue, plus their totals), and a
    product-level breakdown per service: the top `top_n` products by combined
    revenue, and a single "Others" entry summing every remaining product's
    revenue (omitted when a service has `top_n` or fewer distinct products -
    nothing left to bucket).

    Each product's amount (`BookedProductVariant.amount` /
    `SaleItem.subtotal`) gets its price-ratio share of the transaction
    discount deducted, then Exclusive tax added at the transaction's stored
    rate (Inclusive tax is already inside the amount) - see
    _net_product_lines. booking_count / sales_count are DISTINCT bookings /
    sales per service, not product lines.

    Returns {"services": [...], "overall_summary": {...}}.
    - services: list of {service_id, service_name, booking_count,
      sales_count, total_transaction_count, booking_revenue, sales_revenue,
      total_revenue, product_breakdown: [{product_id, product_name,
      booking_amount, sales_amount, total_amount, percentage}, ...]}, sorted
      by total_revenue descending (ties by service_name). Each breakdown's
      total_amount sums to exactly the parent's total_revenue. Completed
      Tailor Orders appear as one extra "Custom Work" entry (service_id None,
      product_breakdown []) - see _custom_work_entry.
      Every active service of the shop is listed, with zeros and an empty
      product_breakdown when it has no activity.
    - overall_summary: distinct booking/sale counts across all services,
      Custom Work (tailor order) count and revenue, and revenue summed
      across every service (Custom Work included in total_transaction_count
      and total_revenue). Amounts are whole numbers when they have no paise,
      otherwise 2-dp numbers.
    """
    booking_lines, sale_lines = _service_product_lines(shop_id, start, end)

    def _empty_service(service_id, service_name):
        return {
            "service_id": service_id,
            "service_name": service_name or "Products",
            "booking_ids": set(),
            "sale_ids": set(),
            "booking_revenue": _ZERO,
            "sales_revenue": _ZERO,
            "products": {},
        }

    # Every active service of the shop is listed, even without activity
    # (zeros). Services with activity that are inactive/deleted are still
    # added below, so no revenue is dropped.
    services = {
        row["id"]: _empty_service(row["id"], row["general_service__name"])
        for row in ShopOwnerService.objects.filter(shop_id=shop_id, is_active=True)
        .values("id", "general_service__name")
    }
    all_booking_ids, all_sale_ids = set(), set()

    def _accumulate(lines, txn_ids_key, all_txn_ids, revenue_key, amount_key):
        for txn_id, service_id, service_name, product_id, product_name, amount in lines:
            if service_id is None or product_id is None:
                continue
            service = services.get(service_id)
            if service is None:
                service = services[service_id] = _empty_service(service_id, service_name)
            # Distinct transactions per service: a booking/sale with several
            # products in the same service counts once for that service.
            service[txn_ids_key].add(txn_id)
            all_txn_ids.add(txn_id)
            service[revenue_key] += amount

            product = service["products"].setdefault(product_id, {
                "product_id": product_id,
                "product_name": product_name or "Unnamed Product",
                "booking_amount": _ZERO,
                "sales_amount": _ZERO,
            })
            product[amount_key] += amount

    _accumulate(booking_lines, "booking_ids", all_booking_ids, "booking_revenue", "booking_amount")
    _accumulate(sale_lines, "sale_ids", all_sale_ids, "sales_revenue", "sales_amount")

    results = []
    for service in services.values():
        total_revenue = service["booking_revenue"] + service["sales_revenue"]
        products = sorted(
            service["products"].values(),
            key=lambda p: p["booking_amount"] + p["sales_amount"],
            reverse=True,
        )

        top_products = products[:top_n]
        remaining = products[top_n:]

        breakdown = [
            {
                "product_id": product["product_id"],
                "product_name": product["product_name"],
                "booking_amount": _as_amount(product["booking_amount"]),
                "sales_amount": _as_amount(product["sales_amount"]),
                "total_amount": _as_amount(product["booking_amount"] + product["sales_amount"]),
                "percentage": _percentage(
                    product["booking_amount"] + product["sales_amount"], total_revenue
                ),
            }
            for product in top_products
        ]

        if remaining:
            others_booking = sum((product["booking_amount"] for product in remaining), _ZERO)
            others_sales = sum((product["sales_amount"] for product in remaining), _ZERO)
            others_total = others_booking + others_sales
            breakdown.append({
                "product_id": None,
                "product_name": OTHERS_LABEL,
                "booking_amount": _as_amount(others_booking),
                "sales_amount": _as_amount(others_sales),
                "total_amount": _as_amount(others_total),
                "percentage": _percentage(others_total, total_revenue),
            })

        booking_count = len(service["booking_ids"])
        sales_count = len(service["sale_ids"])
        results.append({
            "service_id": service["service_id"],
            "service_name": service["service_name"],
            "booking_count": booking_count,
            "sales_count": sales_count,
            "total_transaction_count": booking_count + sales_count,
            "booking_revenue": service["booking_revenue"],
            "sales_revenue": service["sales_revenue"],
            "total_revenue": total_revenue,
            "product_breakdown": breakdown,
        })

    custom_work = _custom_work_entry(shop_id, start, end)
    if custom_work:
        custom_work["total_revenue"] = _dec(custom_work["total_revenue"])
        results.append(custom_work)

    # Highest total_revenue first (Custom Work ranked by its amount like any
    # service); equal totals ordered by service name so the order is stable.
    results.sort(key=lambda entry: (-entry["total_revenue"], entry["service_name"]))

    tailor_order_count = custom_work["total_transaction_count"] if custom_work else 0
    tailor_order_revenue = custom_work["total_revenue"] if custom_work else _ZERO
    overall_summary = {
        # Distinct across all services - a booking/sale spanning several
        # services counts once here.
        "total_booking_count": len(all_booking_ids),
        "total_sales_count": len(all_sale_ids),
        "total_tailor_order_count": tailor_order_count,
        "total_transaction_count": len(all_booking_ids) + len(all_sale_ids) + tailor_order_count,
        "total_booking_revenue": _as_amount(sum((_dec(r["booking_revenue"]) for r in results), _ZERO)),
        "total_sales_revenue": _as_amount(sum((_dec(r["sales_revenue"]) for r in results), _ZERO)),
        "total_tailor_order_revenue": _as_amount(tailor_order_revenue),
        "total_revenue": _as_amount(sum((_dec(r["total_revenue"]) for r in results), _ZERO)),
    }

    for entry in results:
        for key in ("booking_revenue", "sales_revenue", "total_revenue"):
            entry[key] = _as_amount(entry[key])

    return {"services": results, "overall_summary": overall_summary}
