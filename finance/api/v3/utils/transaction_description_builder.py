from collections import defaultdict

from booking_v2.models import BookingV2
from expenses_v2.models import Expenses, ProductVariantExpense
from finance.models import AccountTransfer, Investment, ManualIncome, Withdrawal
from sales.models import Sale
from tailored.models import Tailor, TailorOrder

# transaction_type -> (model to join, values_list fields to fetch)
# Assumes resolve_ledger_reference_ids has already run, so reference_id
# points at the real parent entity for these types (see reference_id_resolver.py).
_BOOKING_TYPES = {"booking_payment", "security_in", "security_out", "refund"}
_TAILOR_ORDER_TYPES = {
    "tailor_order_payment",
    "tailor_order_security_in",
    "tailor_order_refund",
    "tailor_security_refund",
}


def build_transaction_descriptions(entries):
    """
    Mutates each entry's .description in place with a human-readable label
    for the account-history PDF, per transaction_type. Runs one bulk query
    per distinct transaction_type group present (same grouped-bulk-query
    shape as reference_id_resolver.resolve_ledger_reference_ids), so this
    stays O(distinct types) regardless of how many entries there are - no
    N+1. Must run AFTER resolve_ledger_reference_ids.
    """
    by_type = defaultdict(list)
    for entry in entries:
        by_type[entry.transaction_type].append(entry)

    booking_entries = [e for t in _BOOKING_TYPES for e in by_type.get(t, [])]
    if booking_entries:
        # security_out entries with reference_type == "BookingV2" already
        # carry the real booking id but there is no further booking-side
        # data to join beyond the id itself - client_identity/shop_booking_id
        # lookups still apply the same way since reference_id IS the booking id.
        ids = {e.reference_id for e in booking_entries}
        booking_map = {
            row[0]: (row[1], row[2])
            for row in BookingV2.objects.filter(id__in=ids).values_list(
                "id", "shop_booking_id", "client_identity__client_name"
            )
        }
        for entry in booking_entries:
            row = booking_map.get(entry.reference_id)
            shop_booking_id, client_name = row if row else (None, None)
            entry.description = _booking_like_description(
                entry.transaction_type, client_name, shop_booking_id
            )

    sale_entries = by_type.get("sale_payment", [])
    if sale_entries:
        ids = {e.reference_id for e in sale_entries}
        sale_map = {
            row[0]: (row[1], row[2])
            for row in Sale.objects.filter(id__in=ids).values_list("id", "shop_sale_id", "client__client_name")
        }
        for entry in sale_entries:
            row = sale_map.get(entry.reference_id)
            shop_sale_id, client_name = row if row else (None, None)
            if client_name:
                entry.description = f"{client_name} - {shop_sale_id}"
            else:
                entry.description = f"Sales - {shop_sale_id}" if shop_sale_id else "Sales"

    expense_entries = by_type.get("expense", [])
    if expense_entries:
        ids = {e.reference_id for e in expense_entries}
        expense_map = dict(
            Expenses.objects.filter(id__in=ids).values_list("id", "category__name")
        )
        for entry in expense_entries:
            category_name = expense_map.get(entry.reference_id)
            entry.description = (
                f"Expense - {category_name}" if category_name
                else f"Expense - {entry.get_transaction_type_display()}"
            )

    product_expense_entries = by_type.get("product_expense", [])
    if product_expense_entries:
        ids = {e.reference_id for e in product_expense_entries}
        pve_map = {
            row[0]: (row[1], row[2])
            for row in ProductVariantExpense.objects.filter(id__in=ids).values_list(
                "id", "product_variant__product__name", "category__name"
            )
        }
        for entry in product_expense_entries:
            row = pve_map.get(entry.reference_id)
            product_name, category_name = row if row else (None, None)
            if product_name and category_name:
                entry.description = f"Product Expense - {product_name} ({category_name})"
            elif product_name:
                entry.description = f"Product Expense - {product_name}"
            else:
                entry.description = "Product Expense"

    tailor_order_entries = [e for t in _TAILOR_ORDER_TYPES for e in by_type.get(t, [])]
    if tailor_order_entries:
        ids = {e.reference_id for e in tailor_order_entries}
        order_map = {
            row[0]: (row[1], row[2])
            for row in TailorOrder.objects.filter(id__in=ids).values_list(
                "id", "shop_tailor_id", "client__client_name"
            )
        }
        for entry in tailor_order_entries:
            row = order_map.get(entry.reference_id)
            shop_tailor_id, client_name = row if row else (None, None)
            entry.description = _tailor_order_description(
                entry.transaction_type, client_name, shop_tailor_id
            )

    tailor_payment_entries = by_type.get("tailor_payment", [])
    if tailor_payment_entries:
        ids = {e.reference_id for e in tailor_payment_entries}
        tailor_map = dict(Tailor.objects.filter(id__in=ids).values_list("id", "name"))
        for entry in tailor_payment_entries:
            tailor_name = tailor_map.get(entry.reference_id)
            entry.description = (
                f"Tailor Payment - {tailor_name}" if tailor_name else "Tailor Payment"
            )

    manual_income_entries = by_type.get("manual_income", [])
    if manual_income_entries:
        ids = {e.reference_id for e in manual_income_entries}
        mi_map = dict(ManualIncome.objects.filter(id__in=ids).values_list("id", "category__name"))
        for entry in manual_income_entries:
            category_name = mi_map.get(entry.reference_id)
            entry.description = (
                f"Manual Income - {category_name}" if category_name
                else f"Manual Income - {entry.get_transaction_type_display()}"
            )

    investment_entries = by_type.get("investment", [])
    if investment_entries:
        ids = {e.reference_id for e in investment_entries}
        inv_map = dict(Investment.objects.filter(id__in=ids).values_list("id", "investor_display_name"))
        for entry in investment_entries:
            investor_name = inv_map.get(entry.reference_id)
            entry.description = (
                f"Investment by {investor_name}" if investor_name else "Investment"
            )

    withdrawal_entries = by_type.get("withdrawal", [])
    if withdrawal_entries:
        ids = {e.reference_id for e in withdrawal_entries}
        wd_map = dict(Withdrawal.objects.filter(id__in=ids).values_list("id", "withdrawn_by_display_name"))
        for entry in withdrawal_entries:
            withdrawn_by = wd_map.get(entry.reference_id)
            entry.description = (
                f"Withdrawal by {withdrawn_by}" if withdrawn_by else "Withdrawal"
            )

    transfer_in_entries = by_type.get("transfer_in", [])
    transfer_out_entries = by_type.get("transfer_out", [])
    if transfer_in_entries or transfer_out_entries:
        ids = {e.reference_id for e in transfer_in_entries + transfer_out_entries}
        transfer_map = {
            row[0]: (row[1], row[2])
            for row in AccountTransfer.objects.filter(id__in=ids).values_list(
                "id", "from_account__name", "to_account__name"
            )
        }
        for entry in transfer_in_entries:
            row = transfer_map.get(entry.reference_id)
            from_account_name = row[0] if row else None
            entry.description = (
                f"Transfer from {from_account_name}" if from_account_name else "Transfer In"
            )
        for entry in transfer_out_entries:
            row = transfer_map.get(entry.reference_id)
            to_account_name = row[1] if row else None
            entry.description = (
                f"Transfer to {to_account_name}" if to_account_name else "Transfer Out"
            )

    # Any type not explicitly handled above (shouldn't happen given
    # LedgerEntry.TRANSACTION_TYPE_CHOICES, but guards against a future new
    # type shipping without a description rule).
    handled_types = (
        _BOOKING_TYPES | _TAILOR_ORDER_TYPES |
        {"sale_payment", "expense", "product_expense", "tailor_payment",
         "manual_income", "investment", "withdrawal", "transfer_in", "transfer_out"}
    )
    for txn_type, group in by_type.items():
        if txn_type not in handled_types:
            for entry in group:
                entry.description = entry.get_transaction_type_display()


def _booking_like_description(transaction_type, client_name, shop_booking_id):
    if transaction_type == "booking_payment":
        return f"{client_name} - {shop_booking_id}" if client_name else f"Booking - {shop_booking_id}"
    if transaction_type in ("security_in", "security_out"):
        return (
            f"{client_name} Security Deposit - {shop_booking_id}" if client_name
            else f"Booking Security Deposit - {shop_booking_id}"
        )
    if transaction_type == "refund":
        return (
            f"{client_name} Refund - {shop_booking_id}" if client_name
            else f"Booking Refund - {shop_booking_id}"
        )
    return f"Booking - {shop_booking_id}" if shop_booking_id else "Booking"


def _tailor_order_description(transaction_type, client_name, shop_tailor_id):
    if transaction_type == "tailor_order_payment":
        return f"{client_name} - {shop_tailor_id}" if client_name else f"Custom Work - {shop_tailor_id}"
    if transaction_type in ("tailor_order_security_in", "tailor_security_refund"):
        return (
            f"{client_name} Security Deposit - {shop_tailor_id}" if client_name
            else f"Custom Work Security Deposit - {shop_tailor_id}"
        )
    if transaction_type == "tailor_order_refund":
        return (
            f"{client_name} Refund - {shop_tailor_id}" if client_name
            else f"Custom Work Refund - {shop_tailor_id}"
        )
    return f"Custom Work - {shop_tailor_id}" if shop_tailor_id else "Custom Work"
