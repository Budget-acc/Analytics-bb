from decimal import Decimal

from django.db.models import Sum

from finance.models import LedgerEntry


def get_account_history_data(account, start_date=None, end_date=None):
    """
    Shared data-fetch logic for an account's ledger history, used by both
    AccountRetrieveMixin.retrieve (paginated) and the account-history PDF
    export task (unpaginated) - kept in one place so both stay in sync.

    Returns (base_qs, ledger_qs, opening_balance, account_balance):
    - base_qs: all non-deleted LedgerEntry rows for the account (unordered,
      unfiltered by date) - callers use this for page-opening-balance-style
      bounded aggregates.
    - ledger_qs: base_qs filtered to [start_date, end_date] (inclusive) and
      ordered chronologically ascending (oldest first).
    - opening_balance: the account's real balance as of just before
      start_date, so a running balance over ledger_qs doesn't restart at
      zero. Decimal("0.00") if start_date is not given.
    - account_balance: opening_balance + sum(ledger_qs) - the balance as of
      the end of the filtered range.
    """
    base_qs = LedgerEntry.objects.filter(account_id=account.id, deleted__isnull=True)

    opening_balance = Decimal("0.00")
    if start_date:
        opening_balance = base_qs.filter(
            created_at__date__lt=start_date,
        ).aggregate(total=Sum("signed_amount"))["total"] or Decimal("0.00")

    ledger_qs = base_qs
    if start_date:
        ledger_qs = ledger_qs.filter(created_at__date__gte=start_date)
    if end_date:
        ledger_qs = ledger_qs.filter(created_at__date__lte=end_date)
    ledger_qs = ledger_qs.order_by("created_at", "id")

    # Plain Sum() over the (already date-filtered) range - not a Window()
    # over every row - so the DB does one indexed aggregate scan instead of
    # sorting/materializing the whole range to compute a per-row running total.
    range_total = ledger_qs.aggregate(total=Sum("signed_amount"))["total"] or Decimal("0.00")
    account_balance = opening_balance + range_total

    return base_qs, ledger_qs, opening_balance, account_balance
