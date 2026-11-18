from django.db.models import Sum

def update_daily_balance(shop, account, date):
    from finance.models import LedgerEntry, DailyBalance

    # Normalise to IDs so this works whether callers pass model instances
    # (create flow) or raw integer IDs (delete flow via .values()).
    shop_id = shop.id if hasattr(shop, 'id') else shop
    account_id = account.id if hasattr(account, 'id') else account

    entries = LedgerEntry.objects.filter(
        shop_id=shop_id,
        account_id=account_id,
        created_at__date=date,
        deleted__isnull=True
    )

    total_in = entries.filter(direction='in').aggregate(
        total=Sum('amount')
    )['total'] or 0

    total_out = entries.filter(direction='out').aggregate(
        total=Sum('amount')
    )['total'] or 0

    net_change = entries.aggregate(
        total=Sum('signed_amount')
    )['total'] or 0

    # Get previous day closing
    prev = DailyBalance.objects.filter(
        shop_id=shop_id,
        account_id=account_id,
        date__lt=date,
        deleted__isnull=True
    ).order_by('-date').first()

    opening_balance = prev.closing_balance if prev else 0

    closing_balance = opening_balance + net_change

    DailyBalance.objects.update_or_create(
        shop_id=shop_id,
        account_id=account_id,
        date=date,
        defaults={
            'opening_balance': opening_balance,
            'total_in': total_in,
            'total_out': total_out,
            'closing_balance': closing_balance
        }
    )

    # Cascade the updated closing balance forward to all subsequent days.
    # Each day's opening_balance = previous day's closing_balance, so any
    # change to day X must propagate to X+1, X+2, ...
    subsequent = DailyBalance.objects.filter(
        shop_id=shop_id,
        account_id=account_id,
        date__gt=date,
        deleted__isnull=True
    ).order_by('date')

    prev_closing = closing_balance
    for day in subsequent:
        day.opening_balance = prev_closing
        day.closing_balance = prev_closing + (day.total_in - day.total_out)
        day.save(update_fields=['opening_balance', 'closing_balance'])
        prev_closing = day.closing_balance