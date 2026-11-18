from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils.timezone import localtime

from .batch_items import _batch_tailor_items


def _custom_work_dates(shop_id, client):
    from tailored.models import TailorOrder
    qs = TailorOrder.objects.filter(shop_id=shop_id, deleted__isnull=True)
    if client:
        qs = qs.filter(client=client)
    return set(qs.values_list("created_at__date", flat=True).distinct())


def _tailor_payment_accounts(order_ids):
    """
    { order_id: { account_name: Decimal(total) } }
    Sums CustomerPayment.payment_amount per account for each order.
    """
    if not order_ids:
        return {}
    from tailored.models import CustomerPayment
    rows = (
        CustomerPayment.objects.filter(order_id__in=order_ids)
        .values("order_id", "account__name")
        .annotate(total=Sum("payment_amount"))
    )
    result = defaultdict(dict)
    for row in rows:
        acct = row["account__name"] or "Unknown"
        result[row["order_id"]][acct] = Decimal(row["total"] or 0)
    return result


def _fetch_custom_work(shop_id, client, page_dates):
    from tailored.models import TailorOrder

    qs = (
        TailorOrder.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            created_at__date__in=page_dates,
        )
        .select_related("client")
        .order_by("-created_at")
    )
    if client:
        qs = qs.filter(client=client)

    orders = list(qs)
    order_ids = [o.id for o in orders]
    items_map = _batch_tailor_items(order_ids)
    payment_map = _tailor_payment_accounts(order_ids)

    by_date = defaultdict(list)
    for order in orders:
        date_obj = localtime(order.created_at).date()
        paid = order.paid_amount or Decimal(0)
        security = order.security_amount or Decimal(0)
        by_date[date_obj].append({
            "customer_order_type": order.customer_order_type,
            "reference_id": order.id,
            "invoice_id": order.shop_tailor_id,
            "client_name": order.client.client_name if order.client else None,
            "paid_amount": paid,
            "paid_amount_with_security": paid + security,
            "items": items_map.get(order.id, ""),
            "created_at": localtime(order.created_at).strftime("%d-%m-%Y %H:%M:%S"),
        })

    result = {}
    for date_obj, items in by_date.items():
        acc_totals = defaultdict(lambda: Decimal(0))
        total = Decimal(0)
        for item in items:
            for acct, amt in payment_map.get(item["reference_id"], {}).items():
                acc_totals[acct] += amt
                total += amt
        total_with_security = sum(item["paid_amount_with_security"] for item in items)
        result[date_obj] = {
            "accounts": [{"name": n, "amount": str(a)} for n, a in acc_totals.items() if a > 0],
            "total": total,
            "total_amount_with_security": total_with_security,
            "items": items,
        }
    return result
