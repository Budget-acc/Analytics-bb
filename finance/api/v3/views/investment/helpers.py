from django.db import IntegrityError

from expenses_v2.models import ShopCategory
from expenses_v2.api.v3.views.helpers.account_resolution import (
    resolve_account_and_payment_method,
    get_first_validation_error_message,
)

__all__ = [
    'resolve_account_and_payment_method',
    'get_first_validation_error_message',
    'resolve_investment_category',
]


def resolve_investment_category(data, shop_id):
    """
    Resolves category from category_id or category_name, mirroring
    finance.api.v3.views.manual_income.helpers.resolve_income_category.
    ShopCategory.TYPE_CHOICES only supports income/expense/income_and_expense
    (no 'investment' value exists), so auto-created categories use
    'income_and_expense' - a neutral type appropriate for a capital-source
    category that isn't strictly operational income.

    Priority: category_id > category_name.
    Returns (category_or_none, errors_or_none).
    """
    category_id = data.get('category_id')
    category_name = data.get('category_name')

    if category_id:
        category = ShopCategory.objects.filter(
            id=category_id,
            shop_id=shop_id,
            is_active=True,
        ).first()
        if not category:
            return None, {'category_id': ['Invalid category.']}
        return category, None

    if category_name:
        category = ShopCategory.objects.filter(
            shop_id=shop_id,
            name__iexact=category_name.strip(),
        ).first()
        if not category:
            try:
                category = ShopCategory.objects.create(
                    shop_id=shop_id,
                    name=category_name.strip(),
                    type='income_and_expense',
                )
            except IntegrityError:
                category = ShopCategory.objects.filter(
                    shop_id=shop_id,
                    name__iexact=category_name.strip(),
                ).first()
        return category, None

    return None, None
