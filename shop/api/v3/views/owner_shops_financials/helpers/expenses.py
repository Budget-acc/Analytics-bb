from django.db.models.functions import Coalesce, ExtractMonth
from django.db.models import Sum, Value, DecimalField, Q, Case, When, IntegerField
from expenses_v2.models import Expenses, ProductVariantExpense
from decimal import Decimal


def _get_monthly_expense_data(self, shop_ids, year, months):
    """Get monthly expense data for all shops"""
    ZERO_DECIMAL = Value(Decimal("0.00"), output_field=DecimalField())

    expense_date_filter = (
        Q(expense_date_time__year=year, expense_date_time__month__in=months) |
        Q(expense_date_time__isnull=True, date__year=year, date__month__in=months)
    )
    effective_month = Case(
        When(expense_date_time__isnull=False, then=ExtractMonth('expense_date_time')),
        default=ExtractMonth('date'),
        output_field=IntegerField(),
    )

    # Regular expenses
    expenses_data = (
        Expenses.objects
        .filter(shop_id__in=shop_ids)
        .filter(expense_date_filter)
        .annotate(month=effective_month)
        .values("shop_id", "month", "type")
        .annotate(total=Coalesce(Sum("expense", output_field=DecimalField()), ZERO_DECIMAL))
    )

    # Product variant expenses
    pve_data = (
        ProductVariantExpense.objects
        .filter(shop_id__in=shop_ids)
        .filter(expense_date_filter)
        .annotate(month=effective_month)
        .values("shop_id", "month", "type")
        .annotate(total=Coalesce(Sum("expense", output_field=DecimalField()), ZERO_DECIMAL))
    )

    return expenses_data, pve_data

def _get_previous_period_expense(self, shop_ids, year, month=None):
    """Get previous period expense for growth calculation"""
    if month:
        # Previous month
        prev_year = year if month > 1 else year - 1
        prev_month = month - 1 if month > 1 else 12
        months = [prev_month]
        prev_year_filter = prev_year
    else:
        # Previous year
        prev_year_filter = year - 1
        months = list(range(1, 13))

    expenses_data, pve_data = self._get_monthly_expense_data(shop_ids, prev_year_filter, months)

    # Aggregate by shop
    prev_expense_map = {}
    for item in expenses_data:
        shop_id = item["shop_id"]
        prev_expense_map[shop_id] = prev_expense_map.get(shop_id, Decimal("0.00")) + Decimal(str(item["total"]))

    for item in pve_data:
        shop_id = item["shop_id"]
        prev_expense_map[shop_id] = prev_expense_map.get(shop_id, Decimal("0.00")) + Decimal(str(item["total"]))

    return prev_expense_map
