from collections import defaultdict
from decimal import Decimal

from django.utils.timezone import localtime

from .batch_items import _batch_sale_items


def _sales_dates(shop_id, client):
    from sales.models import Sale
    qs = Sale.objects.filter(shop_id=shop_id)
    if client:
        qs = qs.filter(client=client)
    return set(qs.values_list("created_at__date", flat=True).distinct())


def _fetch_sales(shop_id, client, page_dates):
    from sales.models import Sale

    qs = (
        Sale.objects.filter(
            shop_id=shop_id,
            created_at__date__in=page_dates,
        )
        .select_related("client")
        .order_by("-created_at")
    )
    if client:
        qs = qs.filter(client=client)

    sales = list(qs)
    sale_ids = [s.id for s in sales]
    sale_items_map = _batch_sale_items(sale_ids)
    sale_acct_map = _sale_account_map(sale_ids)

    by_date = defaultdict(list)
    for sale in sales:
        date_obj = localtime(sale.created_at).date()
        total_amount = (sale.total_amount or 0) - (sale.discount or 0)
        by_date[date_obj].append({
            "type": "sale",
            "reference_id": sale.id,
            "invoice_id": sale.shop_sale_id,
            "client_name": sale.client.client_name if sale.client else None,
            "account_name": sale_acct_map.get(sale.id),
            "total_amount": total_amount,
            "items": sale_items_map.get(sale.id, ""),
            "created_at": localtime(sale.created_at).strftime("%d-%m-%Y %H:%M:%S"),
        })

    result = {}
    for date_obj, items in by_date.items():
        acc_totals = defaultdict(lambda: Decimal(0))
        total = Decimal(0)
        for item in items:
            acc = item["account_name"] or "Unknown"
            acc_totals[acc] += item["total_amount"]
            total += item["total_amount"]
        result[date_obj] = {
            "accounts": [
                {"name": name, "amount": str(amount)}
                for name, amount in acc_totals.items()
                if amount > 0
            ],
            "total": total,
            "items": sorted(items, key=lambda x: x["created_at"], reverse=True),
        }
    return result


def _sale_account_map(sale_ids):
    """{ sale_id: account_name } — first payment's account per sale."""
    if not sale_ids:
        return {}
    from sales.models import SalesPaymentDetails
    rows = (
        SalesPaymentDetails.objects.filter(sale_id__in=sale_ids)
        .order_by("sale_id", "created_at")
        .values("sale_id", "account__name")
    )
    result = {}
    for row in rows:
        sid = row["sale_id"]
        if sid not in result:
            result[sid] = row["account__name"]
    return result
