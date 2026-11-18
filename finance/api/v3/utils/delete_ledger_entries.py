from django.db import transaction

from .update_daily_balance import update_daily_balance

@transaction.atomic
def delete_ledger_entries(reference_ids, reference_type):
    from finance.models import LedgerEntry, Account

    entries = LedgerEntry.objects.filter(
        reference_id__in=reference_ids,
        reference_type=reference_type,
        deleted__isnull=True
    )

    affected = list(
        entries.values('shop_id', 'account_id', 'created_at')
    )

    entries.delete()

    processed = set()

    for item in affected:
        key = (item['account_id'], item['created_at'].date())

        if key not in processed:
            account = Account.objects.filter(id=item['account_id']).first()

            if account is None:
                processed.add(key)
                continue

            update_daily_balance(
                shop=item['shop_id'],
                account=account,
                date=item['created_at'].date()
            )

            processed.add(key)