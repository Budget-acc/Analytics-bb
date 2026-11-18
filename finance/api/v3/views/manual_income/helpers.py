from django.db import IntegrityError

from expenses_v2.models import ShopCategory
from expenses_v2.api.v3.views.helpers.account_resolution import (
    resolve_account_and_payment_method,
    get_first_validation_error_message,
)

__all__ = [
    'resolve_account_and_payment_method',
    'get_first_validation_error_message',
    'resolve_income_category',
]


def resolve_income_category(data, shop_id):
    """
    Resolves category from category_id or category_name.

    Priority: category_id > category_name.
    Creates a new ShopCategory if category_name is provided and doesn't exist.
    Newly created categories are always typed as `income`.
    Mutates data: sets data['category'] = category.id on success.

    Returns errors dict on failure, None on success.
    """
    category_id = data.pop('category_id', None)
    category_name = data.pop('category_name', None)

    if category_id:
        category = ShopCategory.objects.filter(
            id=category_id,
            shop_id=shop_id,
            is_active=True,
        ).first()
        if not category:
            return {'category_id': ['Invalid category.']}
        data['category'] = category.id
        return None

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
                    type='income',
                )
            except IntegrityError:
                category = ShopCategory.objects.filter(
                    shop_id=shop_id,
                    name__iexact=category_name.strip(),
                ).first()
        data['category'] = category.id
        return None

    return None
