from finance.models import Account


def _payment_method_for_account(account):
    return 'cash' if account.account_type == 'cash' else 'upi'


def _normalize_payment_method(value):
    return (value or '').strip().lower()


def get_first_validation_error_message(errors, default_message):
    if not isinstance(errors, dict):
        return default_message

    for value in errors.values():
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
        if value:
            return str(value)

    return default_message


def resolve_account_and_payment_method(data, shop_id):
    """
    Resolve account and payment method using the shared rules for expense viewsets.

    Returns:
        tuple: (account_or_none, payment_method_or_none, errors_or_none)
    """
    account_id = data.get('account_id')
    payment_method = _normalize_payment_method(data.get('payment_method'))

    if not account_id and not payment_method:
        return None, None, {
            'non_field_errors': ['Either account or payment method must be provided']
        }

    if account_id:
        account = Account.objects.filter(
            id=account_id,
            shop_id=shop_id,
            deleted__isnull=True,
        ).first()
        if not account:
            return None, None, {'account_id': ['Invalid account']}
        if not account.is_active:
            return None, None, {'account_id': ['Account is inactive']}

        resolved_method = payment_method or _payment_method_for_account(account)
        return account, resolved_method, None

    if payment_method == 'cash':
        account = Account.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            account_type='cash',
        ).order_by('-is_default', 'id').first()
        if not account:
            return None, None, {'payment_method': ['No active cash account found for this shop.']}
    elif payment_method in {'upi', 'gpay', 'bank'}:
        account = Account.objects.filter(
            shop_id=shop_id,
            deleted__isnull=True,
            account_type='bank',
        ).order_by('-is_default', 'id').first()
        if not account:
            return None, None, {'payment_method': ['No active bank account found for this shop.']}
    else:
        return None, None, {'payment_method': ['Invalid payment method. Use cash or upi.']}

    return account, payment_method, None
