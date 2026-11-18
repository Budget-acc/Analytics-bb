from collections import OrderedDict
from decimal import Decimal


def group_tax_report_transactions(rows):
    """
    Groups an already-paginated, already-date-ordered slice of tax-report
    rows into "Month YYYY" -> "DD-MM-YYYY" buckets, each with its items
    and a daily_summary (taxable_amount, tax_amount, cgst, sgst) computed
    from the rows present in THIS slice only.

    Rows must already be ordered by date descending (guaranteed by
    build_tax_report_queryset()) so insertion order alone keeps month-year
    and date keys in descending order — no re-sort needed here.

    A date's records may legitimately span two adjacent pages if pagination
    cuts across that date (row-level pagination, not date-bucket-level) —
    each page's daily_summary reflects only what's present on that page,
    not the whole day. The top-level report summary (get_tax_report_summary)
    is unaffected by pagination and remains the authoritative full-range total.

    Dedicated to the Tax Report response shape only — not shared with any
    other endpoint's grouping logic.
    """
    grouped = OrderedDict()
    for row in rows:
        row_date = row["date"]
        month_year_key = row_date.strftime("%B %Y")
        date_key = row_date.strftime("%d-%m-%Y")

        month_bucket = grouped.setdefault(month_year_key, OrderedDict())
        date_bucket = month_bucket.setdefault(date_key, {"items": [], "daily_summary": None})

        item = dict(row)
        item["date"] = date_key
        date_bucket["items"].append(item)

    zero = Decimal("0")
    for month_bucket in grouped.values():
        for bucket in month_bucket.values():
            items = bucket["items"]
            total_taxable = sum((it["taxable_amount"] for it in items), zero)
            total_tax = sum((it["tax_amount"] for it in items), zero)
            bucket["daily_summary"] = {
                "taxable_amount": total_taxable,
                "tax_amount": total_tax,
                "cgst": total_tax / 2,
                "sgst": total_tax / 2,
            }
    return grouped


__all__ = ["group_tax_report_transactions"]
