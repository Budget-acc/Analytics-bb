from django.db import transaction

from .update_daily_balance import update_daily_balance


@transaction.atomic
def create_ledger_entries(payments, shop, transaction_type, reference_type):
    from finance.models import LedgerEntry

    ledger_entries = []

    for p in payments:
        signed_amount = p['amount'] if p['direction'] == 'in' else -p['amount']

        entry = LedgerEntry(
            shop=shop,
            account=p['account'],
            amount=p['amount'],
            signed_amount=signed_amount,
            direction=p['direction'],
            transaction_type=transaction_type,
            payment_method=p.get('payment_method'),
            reference_id=p['reference_id'],
            reference_type=reference_type,
            created_at=p['created_at']
        )
        ledger_entries.append(entry)

    LedgerEntry.objects.bulk_create(ledger_entries)

    # Update daily balances
    updated_pairs = set()

    for p in payments:
        key = (p['account'].id, p['created_at'].date())
        if key not in updated_pairs:
            update_daily_balance(
                shop=shop,
                account=p['account'],
                date=p['created_at'].date()
            )
            updated_pairs.add(key)