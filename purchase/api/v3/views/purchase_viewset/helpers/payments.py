import logging
from collections import defaultdict
from decimal import Decimal

from finance.api.v3.utils import (
    create_ledger_entries as _create_ledger_entries,
    get_available_balance_as_of as _get_available_balance_as_of,
)

logger = logging.getLogger("api_requests")


def _process_advance_payments(self, purchase, shop, payments_data):
    """
    Optional split advance payment against a purchase's purchase_total,
    made at creation time. payments_data: list of
    {"account_id": int, "amount": Decimal, "payment_method": str (optional)}.

    If empty/omitted, this is a no-op - no PurchasePayment/LedgerEntry rows
    are created and no account is required (a purchase is only ever settled
    when money actually changes hands, never automatically).

    Otherwise, validates every entry, checks the summed amount does not
    exceed purchase.purchase_total (never grand_total - additional_charges
    are separately-ledgered on-site expenses excluded from the vendor's
    payable balance), then creates one PurchasePayment row per entry (each
    may use a different account) and one batched outward ledger-entries
    call. Caller must invoke this only after purchase.purchase_total is
    final (i.e. after item resolution for with-products purchases).

    Returns (total_paid: Decimal, error_response).
    """
    from purchase.models import PurchasePayment

    if not payments_data:
        return Decimal("0"), None

    resolved_entries = []
    total = Decimal("0")
    for index, entry in enumerate(payments_data):
        field_prefix = f"paid_amount[{index}]"
        account_id = entry.get("account_id")
        if not account_id:
            return None, self.validation_error_response(
                errors={field_prefix: ["account_id is required for each advance payment entry."]},
                message="account_id is required for each advance payment entry.",
            )
        amount, error = self._to_decimal(entry.get("amount"), f"{field_prefix}.amount")
        if error or amount <= 0:
            return None, self.validation_error_response(
                errors={f"{field_prefix}.amount": ["amount must be a positive number."]},
                message="Invalid advance payment amount.",
            )
        account, derived_payment_method, error = self._resolve_account(
            shop, account_id, field_name=f"{field_prefix}.account_id"
        )
        if error:
            return None, error

        resolved_entries.append({
            "account": account,
            "amount": amount,
            "payment_method": entry.get("payment_method") or derived_payment_method,
        })
        total += amount

    if total > purchase.purchase_total:
        return None, self.validation_error_response(
            errors={"paid_amount": [f"Total advance payment (₹{total}) cannot exceed purchase_total (₹{purchase.purchase_total})."]},
            message="Total advance payment cannot exceed purchase total.",
        )

    # Balance check - same rule as add_payment.py: the selected account must
    # have sufficient available balance as of purchase_date (not the
    # account's current/live balance) to cover the payment, since a later
    # deposit cannot retroactively cover an earlier backdated payment. An
    # account may appear in more than one entry, so this is checked on each
    # account's total requested amount, not per entry - two smaller entries
    # against the same account must not each individually pass a check that
    # their combined total would fail. The account row is locked so two
    # concurrent requests against the same account serialize instead of
    # both reading a stale balance.
    per_account_total = defaultdict(Decimal)
    for resolved in resolved_entries:
        per_account_total[resolved["account"].id] += resolved["amount"]

    for account_id, requested in per_account_total.items():
        available = _get_available_balance_as_of(
            account_id, purchase.purchase_date.date(), lock_for_update=True
        )
        if requested > available:
            date_label = purchase.purchase_date.strftime("%d-%m-%Y")
            return None, self.validation_error_response(
                errors={"paid_amount": [f"Insufficient balance in this account as of {date_label}. Available balance: ₹{available}."]},
                message=f"Insufficient balance in this account as of {date_label}. Available balance: ₹{available}.",
            )

    ledger_payments = []
    for resolved in resolved_entries:
        payment = PurchasePayment.objects.create(
            purchase=purchase,
            amount=resolved["amount"],
            account=resolved["account"],
            payment_method=resolved["payment_method"],
            payment_date=purchase.purchase_date,
        )
        ledger_payments.append({
            "amount": resolved["amount"],
            "direction": "out",
            "account": resolved["account"],
            "payment_method": resolved["payment_method"],
            "reference_id": payment.id,
            "created_at": purchase.purchase_date,
        })

    _create_ledger_entries(
        payments=ledger_payments,
        shop=shop,
        transaction_type="purchase_payment",
        reference_type="PurchasePayment",
    )

    purchase.refresh_from_db(fields=["paid_amount"])
    return purchase.paid_amount, None


__all__ = ["_process_advance_payments"]
