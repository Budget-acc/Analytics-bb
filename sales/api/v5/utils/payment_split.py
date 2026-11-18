"""
Split-payment helpers for Sales API V5.

Supports splitting a sale's advance/paid amount across up to
MAX_SPLIT_PAYMENTS accounts. Each split leg becomes its own
SalesPaymentDetails row, with its own LedgerEntry when it's
account-based (mirrors the existing single-account "new flow"
already used elsewhere in the Sales app).

This module has no dependency on sales/api/v4 — it is self-contained
for V5, per the version-independence requirement.
"""
import logging

logger = logging.getLogger('api_requests')

MAX_SPLIT_PAYMENTS = 3


def validate_split_payments(payments_data, shop_id, max_amount):
    """
    Validate a list of split payment legs.

    payments_data: list of dicts, each optionally with:
        - "id": existing SalesPaymentDetails id (update flow only; ignored on create)
        - "account_id": Account id (new flow, ledger-backed)
        - "payment_method": str (old flow, no ledger; used when account_id absent)
        - "amount": required, > 0

    max_amount: the legs' summed amount must not exceed this. Pass None to
    skip that specific check (e.g. to validate structure/accounts before a
    caller has computed the real ceiling yet) - every other validation
    (leg count, per-leg amount, account existence) still runs.

    Returns (validated_list, total_amount, error_message_or_None).
    Each validated_list entry is {"id" (int or None - see below), "account"
    (Account instance or None), "payment_method", "amount" (int)}.
    A non-empty "id" that isn't a valid integer is rejected with an error
    rather than silently treated as "no id" (which would otherwise create a
    new leg instead of editing the intended one).
    """
    from finance.models import Account

    if not isinstance(payments_data, list) or not payments_data:
        return None, 0, "At least one payment entry is required."

    if len(payments_data) > MAX_SPLIT_PAYMENTS:
        return None, 0, f"A maximum of {MAX_SPLIT_PAYMENTS} payment accounts are supported."

    validated = []
    total = 0

    for entry in payments_data:
        if not isinstance(entry, dict):
            return None, 0, "Invalid payment entry."

        raw_id = entry.get("id")
        leg_id = None
        if raw_id is not None:
            try:
                leg_id = int(raw_id)
            except (TypeError, ValueError):
                return None, 0, "Invalid payment id."

        amount = entry.get("amount")
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return None, 0, "Invalid payment amount."
        if amount <= 0:
            return None, 0, "Each payment amount must be greater than zero."

        account_id = entry.get("account_id")
        payment_method = entry.get("payment_method")
        account = None

        if account_id:
            try:
                account = Account.objects.get(
                    id=account_id,
                    shop_id=shop_id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except Account.DoesNotExist:
                return None, 0, "Invalid or inactive account for this shop."
            payment_method = 'cash' if account.account_type == 'cash' else 'upi'
        elif not payment_method:
            return None, 0, "Each payment entry requires an account_id or payment_method."

        validated.append({
            "id": leg_id,
            "account": account,
            "payment_method": payment_method,
            "amount": amount,
        })
        total += amount

    if max_amount is not None and total > max_amount:
        return None, 0, (
            f"Sum of payments ({total}) cannot exceed the amount ({max_amount})."
        )

    return validated, total, None


def sync_sale_payments(sale, validated_payments, effective_dt, shop):
    """
    Reconcile `sale.payments` (SalesPaymentDetails) against validated_payments:
      - a leg whose id is missing from validated_payments is deleted (row +
        ledger entry, if any);
      - a leg whose id is present but unchanged (amount/account/method) is
        left untouched entirely - no ledger rewrite;
      - a leg whose id is present but changed is updated in place, and its
        ledger entry (if it has/had an account) is deleted then recreated so
        exactly one ledger entry survives per SalesPaymentDetails id;
      - a leg with no id is a new payment account and is created fresh, with
        a new ledger entry if account-based.

    Returns an error message string, or None on success. Intended to run
    inside the caller's @transaction.atomic so any raised exception rolls
    back everything synced so far.
    """
    from sales.models import SalesPaymentDetails
    from finance.api.v3.utils import (
        create_ledger_entries as _create_ledger_entries,
        delete_ledger_entries as _delete_ledger_entries,
        update_daily_balance as _update_daily_balance,
    )

    existing_by_id = {p.id: p for p in sale.payments.all()}
    kept_ids = {leg["id"] for leg in validated_payments if leg.get("id")}

    # Collected across every leg so the ledger writes below happen as one
    # batched create + one batched delete (plus de-duplicated daily-balance
    # recalculations) instead of up to 3 separate round trips of each -
    # mirrors the batching already done for multi-payment flows elsewhere
    # (e.g. booking_v2's _create_payment_records).
    ledger_ids_to_delete = []
    balance_pairs_to_update = set()
    ledger_payments_to_create = []

    # Remove legs no longer present in the request.
    for pid, payment in existing_by_id.items():
        if pid in kept_ids:
            continue
        if payment.account_id:
            ledger_ids_to_delete.append(pid)
            balance_pairs_to_update.add((payment.account_id, sale.sale_date))
        payment.delete()

    for leg in validated_payments:
        leg_id = leg.get("id")
        account = leg["account"]
        payment_method = leg["payment_method"]
        amount = leg["amount"]

        existing = existing_by_id.get(leg_id) if leg_id else None

        if existing:
            new_account_id = account.id if account else None
            unchanged = (
                existing.payment_amount == amount
                and existing.account_id == new_account_id
                and existing.payment_method == payment_method
            )
            if unchanged:
                continue

            old_account_id = existing.account_id
            existing.payment_amount = amount
            existing.payment_method = payment_method
            existing.account = account
            existing.save(update_fields=["payment_amount", "payment_method", "account"])

            if old_account_id:
                ledger_ids_to_delete.append(existing.id)
                balance_pairs_to_update.add((old_account_id, sale.sale_date))

            if account:
                ledger_payments_to_create.append({
                    'amount': amount,
                    'direction': 'in',
                    'account': account,
                    'payment_method': payment_method,
                    'reference_id': existing.id,
                    'created_at': effective_dt,
                })
        else:
            payment_record = SalesPaymentDetails.objects.create(
                sale=sale,
                payment_amount=amount,
                payment_method=payment_method,
                account=account,
            )
            if account:
                ledger_payments_to_create.append({
                    'amount': amount,
                    'direction': 'in',
                    'account': account,
                    'payment_method': payment_method,
                    'reference_id': payment_record.id,
                    'created_at': effective_dt,
                })

    if ledger_ids_to_delete:
        _delete_ledger_entries(reference_ids=ledger_ids_to_delete, reference_type='SalesPaymentDetails')
        for account_id, date in balance_pairs_to_update:
            _update_daily_balance(shop=sale.shop_id, account=account_id, date=date)

    if ledger_payments_to_create:
        _create_ledger_entries(
            payments=ledger_payments_to_create,
            shop=shop,
            transaction_type='sale_payment',
            reference_type='SalesPaymentDetails',
        )

    return None
