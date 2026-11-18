from datetime import datetime

from django.db.models import Sum

from finance.models import Account, LedgerEntry

__all__ = ['get_available_balance_as_of']


def get_available_balance_as_of(account_id, as_of_date, exclude_withdrawal_id=None, lock_for_update=False, exclude_reference=None):
    """
    Available balance for an account as of the end of as_of_date - i.e. the
    sum of every non-deleted LedgerEntry posted on or before that date.

    as_of_date may also be an aware datetime: then only entries posted at or
    before that exact moment count, so a later same-day inflow cannot cover
    an earlier (e.g. timestamped, backdated) outflow.

    Shared across finance/expense flows that need to validate an account's
    balance before a debit-like operation:
    - Backdated withdrawals must be validated against this historical
      balance, not the account's live/current total, since a later-dated
      deposit cannot retroactively cover an earlier withdrawal.
    - Deleting an Investment/ManualIncome (removing a previously posted
      inflow) or creating an Expense (posting a new outflow) instead check
      the account's current/live balance by passing today's date.

    exclude_withdrawal_id excludes this withdrawal's own existing ledger
    entry (if any) from the sum - used on update, so re-validating a
    withdrawal's own amount/date never counts its own prior contribution
    against itself, regardless of whether the account or date changed.

    exclude_reference is the generic form of the same idea: a
    (reference_type, reference_id) tuple whose own ledger entry is left out
    of the sum - used when updating other outflow records (e.g. expenses).

    When lock_for_update=True, the Account row is locked first
    (select_for_update) so concurrent requests against the same account
    serialize instead of racing past a stale read.
    """
    if lock_for_update:
        Account.objects.select_for_update().get(id=account_id)

    # datetime is a subclass of date, so check it first
    if isinstance(as_of_date, datetime):
        as_of_filter = {'created_at__lte': as_of_date}
    else:
        as_of_filter = {'created_at__date__lte': as_of_date}

    qs = LedgerEntry.objects.filter(
        account_id=account_id,
        deleted__isnull=True,
        **as_of_filter,
    )
    if exclude_withdrawal_id is not None:
        qs = qs.exclude(reference_type='Withdrawal', reference_id=exclude_withdrawal_id)
    if exclude_reference is not None:
        ref_type, ref_id = exclude_reference
        qs = qs.exclude(reference_type=ref_type, reference_id=ref_id)

    total = qs.aggregate(total=Sum('signed_amount'))['total']
    return total or 0
