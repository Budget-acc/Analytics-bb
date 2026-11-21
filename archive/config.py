"""
All archive rules in one place: which tables move, how they link, which
ledger rows belong to them, and which date / amount column to report.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

BATCH_SIZE = getattr(settings, "ARCHIVE_BATCH_SIZE", 500)

# Order the kinds run in for each shop. daily_balance always runs last,
# and only when every other batch of that shop succeeded.
KINDS = ["booking", "sale", "expense", "product_expense"]
DAILY_BALANCE_KIND = "daily_balance"

# Main table of each kind.
MAIN_TABLE = {
    "booking": "booking_v2_bookingv2",
    "sale": "sales_sale",
    "expense": "expenses_v2_expenses",
    "product_expense": "expenses_v2_productvariantexpense",
    "daily_balance": "finance_dailybalance",
}

# Child tables that move with the main record: (table, column pointing at the main row).
CHILD_TABLES = {
    "booking": [
        ("booking_v2_bookedproductvariant", "booking_id"),
        ("booking_v2_paymentdetailsv2", "booking_id"),
        ("booking_v2_securitydepositpayment", "booking_id"),
        ("booking_v2_securitydepositadjustment", "booking_id"),
        ("booking_v2_refunddetails", "booking_id"),
        ("booking_v2_bookingadditionalcharge", "booking_id"),
        ("booking_v2_clientrequirementv2", "booking_id"),
    ],
    "sale": [
        ("sales_saleitem", "sale_id"),
        ("sales_salespaymentdetails", "sale_id"),
    ],
    "expense": [
        ("expenses_v2_productvariantexpense", "linked_expense_id"),
    ],
    "product_expense": [],
    "daily_balance": [],
}

# finance_ledgerentry.reference_type for rows of each moved table.
LEDGER_REFERENCE_TYPE = {
    "booking_v2_bookingv2": "BookingV2",
    "booking_v2_paymentdetailsv2": "PaymentDetailsV2",
    "booking_v2_securitydepositpayment": "SecurityDepositPayment",
    "booking_v2_securitydepositadjustment": "SecurityDepositAdjustment",
    "booking_v2_refunddetails": "RefundDetails",
    "sales_sale": "Sale",
    "sales_salespaymentdetails": "SalesPaymentDetails",
    "expenses_v2_expenses": "Expenses",
    "expenses_v2_productvariantexpense": "ProductVariantExpense",
}

# Customer tables: copied always, deleted from live only when nothing uses them any more.
CLIENT_TABLES = {"booking_v2_client", "booking_v2_clientv2"}

# Foreign keys INTO moved rows from rows that stay in live. Set to NULL before delete.
NULLIFY_REFERENCES = {
    ("tailored_tailororder", "created_booking_id"),
    ("shop_shopactivitylog", "restored_from_activity_id"),
}

# Models never copied as reference data (they exist in both apps already).
SKIP_REFERENCE_MODELS = {
    "contenttypes.contenttype",
    "auth.permission",
    "auth.group",
    "sessions.session",
    "admin.logentry",
}

# Business date and amount column per table, used for date_from / date_to / total_amount
# in manifest.json. A table missing here simply gets no dates / total.
DATE_FIELD = {
    "booking_v2_bookingv2": "completed_at",
    "booking_v2_paymentdetailsv2": "created_at",
    "booking_v2_securitydepositpayment": "created_at",
    "booking_v2_securitydepositadjustment": "created_at",
    "booking_v2_refunddetails": "created_at",
    "sales_sale": "sale_date",
    "sales_salespaymentdetails": "created_at",
    "expenses_v2_expenses": "expense_date_time",
    "expenses_v2_productvariantexpense": "expense_date_time",
    "finance_ledgerentry": "created_at",
    "finance_dailybalance": "date",
    "shop_shopactivitylog": "created_at",
}
AMOUNT_FIELD = {
    "booking_v2_bookingv2": "total_amount",
    "booking_v2_paymentdetailsv2": "payment_amount",
    "booking_v2_refunddetails": "refunded_amount",
    "sales_sale": "total_amount",
    "sales_salespaymentdetails": "payment_amount",
    "expenses_v2_expenses": "expense",
    "expenses_v2_productvariantexpense": "expense",
    "finance_ledgerentry": "signed_amount",
    "finance_dailybalance": "closing_balance",
}

# Postgres safety limits for the delete transaction.
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "120s"


def archive_shop_ids():
    """Shops to archive, from ARCHIVE_SHOP_IDS in the env file (for example "66,70,81")."""
    return list(getattr(settings, "ARCHIVE_SHOP_IDS", []))


def is_archived_shop(shop_id):
    try:
        return int(shop_id) in archive_shop_ids()
    except (TypeError, ValueError):
        return False


def archive_cutoff(now=None):
    """
    00:00 (local time, IST) of the oldest day the live app keeps.
    ARCHIVE_KEEP_DAYS = 1 keeps yesterday and today, so the cutoff is 00:00 of yesterday.
    Everything before the cutoff moves.
    """
    keep_days = int(getattr(settings, "ARCHIVE_KEEP_DAYS", 1))
    first_kept_day = timezone.localdate(now) - timedelta(days=keep_days)
    return timezone.make_aware(datetime.combine(first_kept_day, time.min))