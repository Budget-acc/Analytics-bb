from django.db import IntegrityError

from expenses_v2.models import ShopCategory
from tailored.api.v3.utils.account_resolution import (
    resolve_account_and_payment_method,
    get_first_validation_error_message,
)
from tailored.api.v3.utils.tailor_payment_summary import get_tailor_remaining_payable

__all__ = [
    'resolve_account_and_payment_method',
    'get_first_validation_error_message',
    'validate_remaining_payable',
    'resolve_tailor_payment_category',
]


def validate_remaining_payable(tailor_id, shop_id, amount, exclude_payment_id=None):
    """
    Ensures `amount` does not exceed the tailor's remaining payable balance
    (total charged on completed orders minus total already paid).

    Pass exclude_payment_id when re-validating an existing payment during an
    update, so the payment being edited doesn't count against its own total.

    Returns an errors dict on failure, None on success.
    """
    remaining = get_tailor_remaining_payable(
        tailor_id, shop_id, exclude_payment_id=exclude_payment_id
    )
    if amount > remaining:
        return {
            'amount': [
                f"Payment amount exceeds the tailor's remaining pending amount ({remaining})."
            ]
        }
    return None


def resolve_tailor_payment_category(data, shop_id):
    """
    Resolves category from category_id or category_name.

    Priority: category_id > category_name.
    Creates a new ShopCategory if category_name is provided and doesn't exist.
    Newly created categories are always typed as `expense`, since a tailor
    payment is an outward/expense transaction.
    Mutates data: sets data['category'] = category.id on success.
    If neither category_id nor category_name is provided, does nothing -
    category is optional on TailorPayment.

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
                    type='expense',
                )
            except IntegrityError:
                category = ShopCategory.objects.filter(
                    shop_id=shop_id,
                    name__iexact=category_name.strip(),
                ).first()
        data['category'] = category.id
        return None

    return None
