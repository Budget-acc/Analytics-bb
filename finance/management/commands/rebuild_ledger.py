"""
Full Ledger Rebuild Command
===========================

PURPOSE
-------
Authoritative tool for reconstructing all LedgerEntry and DailyBalance records from raw
source transaction tables. Run this when:
  - Ledger data has drifted from source tables (e.g. after a data migration)
  - A new transaction source has been added retroactively
  - Daily balances are inconsistent or corrupted

PRODUCTION WARNING
------------------
Running this command for a shop deletes ALL existing LedgerEntry and DailyBalance records
for that shop and rebuilds from scratch inside a single atomic transaction. The shop
remains in a consistent state at all times (old data is only removed after the new rebuild
succeeds), but expect elevated DB load. Run during off-peak hours for large datasets.

TRANSACTION SOURCES  (14 ledger entry types from 13 tables)
------------------------------------------------------------
Money In:
  1.  SalesPaymentDetails          -> sale_payment              created_at
  2.  PaymentDetailsV2             -> booking_payment           created_at          skip: booking deleted
  3.  CustomerPayment              -> tailor_order_payment      created_at          skip: order deleted/converted
  4.  TailorOrderSecurityPayment   -> tailor_order_security_in  paid_at*            filter: is_paid=True; skip: order deleted/converted
  5.  SecurityDepositPayment       -> security_in               paid_at*            filter: is_paid=True; skip: booking deleted
  6.  ManualIncome                 -> manual_income             transaction_date_time  skip: soft-deleted
  7.  AccountTransfer              -> transfer_in               transfer_date_time  skip: soft-deleted

Money Out:
  8.  SecurityDepositAdjustment    -> security_out              created_at          filter: adjustment_type=refund; skip: booking deleted
  9.  TailorOrderSecurityDepositAdjustment -> tailor_security_refund created_at    filter: adjustment_type=refunded; skip: order deleted
  10. RefundDetails                -> refund                    created_at          skip: booking deleted
  11. Expenses                     -> expense                   expense_date_time*  skip: soft-deleted
  12. ProductVariantExpense        -> expense                   expense_date_time*  skip: soft-deleted
  13. AccountTransfer              -> transfer_out              transfer_date_time  skip: soft-deleted
  14. TailorOrderRefund             -> tailor_order_refund      created_at          skip: order converted

  * Falls back to created_at when the preferred field is null.

DATETIME RULE
-------------
Every ledger entry uses the actual business transaction datetime, NOT the record creation
timestamp. This ensures that if a transaction is recorded retroactively, daily balances
still reflect the correct historical date.

OPERATION SEQUENCE (per shop)
------------------------------
  1. Delete all LedgerEntry rows for the shop.
  2. Delete all DailyBalance rows for the shop.
  3. Recreate LedgerEntry rows from all 12 source tables using bulk_create (batched).
  4. Call recalculate_daily_balances_for_shop() to rebuild DailyBalance in one
     chronological pass, ensuring closing_balance[day N] == opening_balance[day N+1].

  Steps 1-4 execute inside a single transaction.atomic() block per shop. A failure on
  any source table rolls back the entire shop rebuild, leaving the previous state intact.

SKIP TRACKING
-------------
  Every skipped transaction is recorded with: source, record_id, shop_id, account_id,
  amount, transaction_datetime, and reason. A summary table is printed after each shop
  and at the end of the full run.

SPECIAL CASES
-------------
  AccountTransfer: creates TWO ledger entries per record (transfer_out + transfer_in).
  TailorOrderSecurityDepositAdjustment: only 'refunded' type -> Money Out.
                                        'deducted' type is excluded (no cash movement).
  TailorOrderSecurityPayment / SecurityDepositPayment: only is_paid=True records included.

N+1 QUERY PREVENTION
---------------------
  All querysets use select_related() to pre-fetch FKs accessed inside the loop.
  Processing is chunked via batch_size to cap memory usage.

SAFEGUARDS
----------
  --dry-run   Counts what would be created without writing any data.
  --force     Skips the confirmation prompt (required for automated pipelines).
  --shop-id   Rebuilds a single shop; omit to rebuild all shops.

USAGE
-----
  python manage.py rebuild_ledger --shop-id 115 --dry-run
  python manage.py rebuild_ledger --shop-id 115 --force
  python manage.py rebuild_ledger --force
  python manage.py rebuild_ledger --force --batch-size 200
"""

import logging
from dataclasses import dataclass, field as dc_field
from decimal import Decimal
from typing import List, Optional

from django.core.management.base import BaseCommand
from django.db import transaction

from booking_v2.models import (
    BookingV2,
    PaymentDetailsV2,
    RefundDetails,
    SecurityDepositAdjustment,
    SecurityDepositPayment,
)
from expenses_v2.models import Expenses, ProductVariantExpense
from finance.daily_balance_utils import recalculate_daily_balances_for_shop
from finance.models import Account, AccountTransfer, DailyBalance, LedgerEntry, ManualIncome
from sales.models import Sale, SalesPaymentDetails
from shop.models import Shop
from tailored.models import (
    CustomerPayment,
    TailorOrder,
    TailorOrderRefund,                         # NEW
    TailorOrderSecurityDepositAdjustment,
    TailorOrderSecurityPayment,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skip reason constants
# ---------------------------------------------------------------------------

SKIP_BOOKING_DELETED = 'booking soft-deleted'
SKIP_ORDER_DELETED   = 'order soft-deleted'
SKIP_ORDER_CONVERTED = 'order converted to booking'
SKIP_NO_ACCOUNT      = 'no valid account resolved'


# ---------------------------------------------------------------------------
# Skip tracking data structures
# ---------------------------------------------------------------------------

@dataclass
class _SkipRecord:
    """One skipped transaction captured for the rebuild summary report."""
    source:               str
    record_id:            int
    shop_id:              int
    account_id:           Optional[int]
    amount:               Decimal
    transaction_datetime: object   # datetime or None
    reason:               str


@dataclass
class _SourceStats:
    """Accumulated counters for one transaction source."""
    label:            str
    scanned:          int     = 0
    created:          int     = 0
    skipped:          int     = 0
    amount_processed: Decimal = dc_field(default_factory=Decimal)
    amount_skipped:   Decimal = dc_field(default_factory=Decimal)
    skips:            List[_SkipRecord] = dc_field(default_factory=list)


class Command(BaseCommand):
    help = 'Full ledger rebuild: clears existing entries and recreates from all source tables.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--shop-id',
            type=int,
            default=None,
            help='Process only this shop (default: all shops)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Records per bulk_create batch (default: 500)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be created without writing to the database',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt',
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        shop_id    = options['shop_id']
        batch_size = options['batch_size']
        dry_run    = options['dry_run']
        force      = options['force']

        if shop_id:
            shops = Shop.objects.filter(id=shop_id)
            if not shops.exists():
                self.stdout.write(self.style.ERROR(f'Shop {shop_id} not found'))
                return
        else:
            shops = Shop.objects.all()

        total_shops = shops.count()

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no database changes will be made'))

        if not dry_run and not force:
            self.stdout.write(self.style.WARNING(
                f'WARNING: This will DELETE all LedgerEntry and DailyBalance records for '
                f'{total_shops} shop(s) and rebuild from scratch.'
            ))
            confirm = input('Proceed? (yes/no): ').strip().lower()
            if confirm not in ('yes', 'y'):
                self.stdout.write(self.style.ERROR('Cancelled.'))
                return

        grand_created = 0
        grand_skipped = 0

        # Detailed per-source reporting and skip log only for single-shop runs.
        # All-shops runs stay minimal to avoid excessive output and maintain performance.
        verbose = shop_id is not None

        for shop in shops:
            self.stdout.write(f'\nShop: {shop.name} (ID: {shop.id})')
            created, skipped = self._rebuild_shop(shop, batch_size, dry_run, verbose)
            grand_created += created
            grand_skipped += skipped

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('REBUILD COMPLETE'))
        self.stdout.write(f'  Total shops processed : {total_shops}')
        self.stdout.write(f'  Total entries created : {grand_created}')
        if grand_skipped:
            self.stdout.write(self.style.WARNING(
                f'  Total entries skipped : {grand_skipped}'
                + (' — review skip log above' if verbose else ' — rerun with --shop-id for details')
            ))
        else:
            self.stdout.write(self.style.SUCCESS('  No transactions skipped.'))

    # ------------------------------------------------------------------
    # Per-shop rebuild
    # ------------------------------------------------------------------

    def _rebuild_shop(self, shop, batch_size, dry_run, verbose=False):
        accounts = self._get_or_create_accounts(shop, dry_run)
        if not accounts['cash'] and not accounts['bank']:
            self.stdout.write(self.style.WARNING('  No accounts found — skipping'))
            return 0, 0

        if dry_run:
            counts = self._count_entries(shop)
            for label, count in counts.items():
                self.stdout.write(f'  {label}: {count}')
            self.stdout.write(f'  Total would create: {sum(counts.values())}')
            return 0, 0

        with transaction.atomic():
            # Step 1: clear existing data
            deleted_entries, _ = LedgerEntry.objects.filter(shop=shop).delete()
            deleted_balances, _ = DailyBalance.objects.filter(shop=shop).delete()
            self.stdout.write(
                f'  Cleared {deleted_entries} ledger entries, {deleted_balances} daily balances'
            )

            # Step 2: rebuild from all source tables; collect per-source stats
            all_stats = []
            all_stats.append(self._process_sales_payments(shop, accounts, batch_size))
            all_stats.append(self._process_booking_payments(shop, accounts, batch_size))
            all_stats.append(self._process_tailor_payments(shop, accounts, batch_size))
            all_stats.append(self._process_tailor_security(shop, accounts, batch_size))        # FIXED: is_paid + paid_at
            all_stats.append(self._process_security_deposits(shop, accounts, batch_size))      # FIXED: is_paid + paid_at
            all_stats.append(self._process_security_adjustments(shop, accounts, batch_size))
            all_stats.append(self._process_refunds(shop, accounts, batch_size))
            all_stats.append(self._process_tailor_refunds(shop, accounts, batch_size))           # NEW
            all_stats.append(self._process_expenses(shop, accounts, batch_size))               # FIXED: expense_date_time
            all_stats.append(self._process_variant_expenses(shop, accounts, batch_size))       # FIXED: expense_date_time
            # NEW sources
            all_stats.append(self._process_manual_incomes(shop, accounts, batch_size))
            all_stats.append(self._process_tailor_security_adjustments(shop, accounts, batch_size))
            all_stats.append(self._process_account_transfers(shop, accounts, batch_size))

            total_created = sum(s.created for s in all_stats)
            total_skipped = sum(s.skipped for s in all_stats)

            self.stdout.write(f'  Created {total_created} ledger entries')
            self._print_shop_report(all_stats, verbose)

            # Step 3: recalculate daily balances in a single chronological pass
            recalculate_daily_balances_for_shop(shop, clear_existing=False)
            self.stdout.write('  Daily balances recalculated')

        return total_created, total_skipped

    # ------------------------------------------------------------------
    # Table processors — existing (with fixes noted inline)
    # ------------------------------------------------------------------

    def _process_sales_payments(self, shop, accounts, batch_size):
        """SalesPaymentDetails -> sale_payment (in). Sale has no soft-delete; include all."""
        qs = (
            SalesPaymentDetails.objects
            .filter(sale__shop=shop)
            .select_related('sale', 'account')
            .order_by('created_at', 'id')
        )
        def _entry(obj):
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.payment_amount,
                direction='in',
                transaction_type='sale_payment',
                reference_type='SalesPaymentDetails',
                reference_id=obj.id,
                accounts=accounts,
            )
        return self._bulk_process(qs, _entry, batch_size, 'sale_payment', shop)

    def _process_booking_payments(self, shop, accounts, batch_size):
        """PaymentDetailsV2 -> booking_payment (in). Skip if booking deleted."""
        qs = (
            PaymentDetailsV2.objects
            .filter(booking__shop=shop)
            .select_related('booking', 'account')
            .order_by('created_at', 'id')
        )
        def _entry(obj):
            if obj.booking.deleted is not None:
                return None, SKIP_BOOKING_DELETED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.payment_amount,
                direction='in',
                transaction_type='booking_payment',
                reference_type='PaymentDetailsV2',
                reference_id=obj.id,
                accounts=accounts,
            )
        return self._bulk_process(qs, _entry, batch_size, 'booking_payment', shop)

    def _process_tailor_payments(self, shop, accounts, batch_size):
        """CustomerPayment -> tailor_order_payment (in).
        Skip if order deleted OR order converted to a booking."""
        qs = (
            CustomerPayment.objects
            .filter(order__shop=shop)
            .select_related('order', 'account')
            .order_by('created_at', 'id')
        )
        def _entry(obj):
            order = obj.order
            if getattr(order, 'deleted', None) is not None:
                return None, SKIP_ORDER_DELETED
            if order.created_booking_id is not None:
                return None, SKIP_ORDER_CONVERTED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.payment_amount,
                direction='in',
                transaction_type='tailor_order_payment',
                reference_type='CustomerPayment',
                reference_id=obj.id,
                accounts=accounts,
            )
        return self._bulk_process(qs, _entry, batch_size, 'tailor_order_payment', shop)

    def _process_tailor_security(self, shop, accounts, batch_size):
        """TailorOrderSecurityPayment -> tailor_order_security_in (in).
        Only confirmed payments (is_paid=True). Uses paid_at as the ledger datetime.
        Skip if order deleted or converted to a booking.

        CHANGED: added is_paid=True filter; ledger datetime now uses paid_at (fallback: created_at).
        """
        qs = (
            TailorOrderSecurityPayment.objects
            .filter(order__shop=shop, is_paid=True)          # CHANGED: added is_paid=True
            .select_related('order', 'account')
            .order_by('paid_at', 'id')                       # CHANGED: ordered by business datetime
        )
        def _entry(obj):
            order = obj.order
            if getattr(order, 'deleted', None) is not None:
                return None, SKIP_ORDER_DELETED
            if order.created_booking_id is not None:
                return None, SKIP_ORDER_CONVERTED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.amount,
                direction='in',
                transaction_type='tailor_order_security_in',
                reference_type='TailorOrderSecurity',
                reference_id=order.id,
                accounts=accounts,
                transaction_datetime=obj.paid_at or obj.created_at,   # CHANGED
            )
        return self._bulk_process(qs, _entry, batch_size, 'tailor_order_security_in', shop)

    def _process_security_deposits(self, shop, accounts, batch_size):
        """SecurityDepositPayment -> security_in (in).
        Only confirmed payments (is_paid=True). Uses paid_at as the ledger datetime.
        Skip if booking deleted.

        CHANGED: added is_paid=True filter; ledger datetime now uses paid_at (fallback: created_at).
        """
        qs = (
            SecurityDepositPayment.objects
            .filter(booking__shop=shop, is_paid=True)         # CHANGED: added is_paid=True
            .select_related('booking', 'account')
            .order_by('paid_at', 'id')                        # CHANGED: ordered by business datetime
        )
        def _entry(obj):
            if obj.booking.deleted is not None:
                return None, SKIP_BOOKING_DELETED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.amount,
                direction='in',
                transaction_type='security_in',
                reference_type='SecurityDepositPayment',
                reference_id=obj.id,
                accounts=accounts,
                transaction_datetime=obj.paid_at or obj.created_at,   # CHANGED
            )
        return self._bulk_process(qs, _entry, batch_size, 'security_in', shop)

    def _process_security_adjustments(self, shop, accounts, batch_size):
        """SecurityDepositAdjustment (refund only) -> security_out (out).
        Skip if booking deleted or adjustment_type != 'refund'."""
        qs = (
            SecurityDepositAdjustment.objects
            .filter(booking__shop=shop, adjustment_type='refund')
            .select_related('booking', 'account')
            .order_by('created_at', 'id')
        )
        def _entry(obj):
            if obj.booking.deleted is not None:
                return None, SKIP_BOOKING_DELETED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.amount,
                direction='out',
                transaction_type='security_out',
                reference_type='SecurityDepositAdjustment',
                reference_id=obj.id,
                accounts=accounts,
            )
        return self._bulk_process(qs, _entry, batch_size, 'security_out', shop)

    def _process_refunds(self, shop, accounts, batch_size):
        """RefundDetails -> refund (out). Skip if booking deleted."""
        qs = (
            RefundDetails.objects
            .filter(booking__shop=shop)
            .select_related('booking', 'account')
            .order_by('created_at', 'id')
        )
        def _entry(obj):
            if obj.booking.deleted is not None:
                return None, SKIP_BOOKING_DELETED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.refunded_amount,
                direction='out',
                transaction_type='refund',
                reference_type='RefundDetails',
                reference_id=obj.id,
                accounts=accounts,
            )
        return self._bulk_process(qs, _entry, batch_size, 'refund', shop)

    def _process_tailor_refunds(self, shop, accounts, batch_size):
        """TailorOrderRefund -> tailor_order_refund (out).
        Uses created_at as the ledger datetime.
        Skip if the related TailorOrder has been converted to a Booking.

        NEW source added in this rebuild version.
        """
        qs = (
            TailorOrderRefund.objects
            .filter(order__shop=shop)
            .select_related('order', 'account')
            .order_by('created_at', 'id')
        )
        def _entry(obj):
            if getattr(obj.order, 'created_booking_id', None) is not None:
                return None, SKIP_ORDER_CONVERTED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.refunded_amount,
                direction='out',
                transaction_type='tailor_order_refund',
                reference_type='TailorOrderRefund',
                reference_id=obj.id,
                accounts=accounts,
                transaction_datetime=obj.created_at,
            )
        return self._bulk_process(qs, _entry, batch_size, 'tailor_order_refund', shop)

    def _process_expenses(self, shop, accounts, batch_size):
        """Expenses -> expense (out). Skip if deleted.
        Uses expense_date_time as the ledger datetime (falls back to created_at when null).

        CHANGED: ledger datetime now uses expense_date_time instead of created_at.
        """
        qs = (
            Expenses.objects
            .filter(shop=shop, deleted__isnull=True)
            .select_related('account')
            .order_by('expense_date_time', 'id')              # CHANGED: ordered by business datetime
        )
        def _entry(obj):
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.expense,
                direction='out',
                transaction_type='expense',
                reference_type='Expenses',
                reference_id=obj.id,
                accounts=accounts,
                transaction_datetime=obj.expense_date_time or obj.created_at,  # CHANGED
            )
        return self._bulk_process(qs, _entry, batch_size, 'expense (Expenses)', shop)

    def _process_variant_expenses(self, shop, accounts, batch_size):
        """ProductVariantExpense -> expense (out). Skip if deleted.
        Uses expense_date_time as the ledger datetime (falls back to created_at when null).

        CHANGED: ledger datetime now uses expense_date_time instead of created_at.
        """
        qs = (
            ProductVariantExpense.objects
            .filter(shop=shop, deleted__isnull=True)
            .select_related('account')
            .order_by('expense_date_time', 'id')              # CHANGED: ordered by business datetime
        )
        def _entry(obj):
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.expense,
                direction='out',
                transaction_type='expense',
                reference_type='ProductVariantExpense',
                reference_id=obj.id,
                accounts=accounts,
                transaction_datetime=obj.expense_date_time or obj.created_at,  # CHANGED
            )
        return self._bulk_process(qs, _entry, batch_size, 'expense (ProductVariantExpense)', shop)

    # ------------------------------------------------------------------
    # Table processors — NEW sources
    # ------------------------------------------------------------------

    def _process_manual_incomes(self, shop, accounts, batch_size):
        """ManualIncome -> manual_income (in). Skip if soft-deleted.
        Uses transaction_date_time (the actual income date) as the ledger datetime.
        Never uses created_at — the record may be entered retroactively.

        NEW source added in this rebuild version.
        """
        qs = (
            ManualIncome.objects
            .filter(shop=shop, deleted__isnull=True)
            .select_related('account')
            .order_by('transaction_date_time', 'id')
        )
        def _entry(obj):
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.amount,
                direction='in',
                transaction_type='manual_income',
                reference_type='ManualIncome',
                reference_id=obj.id,
                accounts=accounts,
                transaction_datetime=obj.transaction_date_time,
            )
        return self._bulk_process(qs, _entry, batch_size, 'manual_income', shop)

    def _process_tailor_security_adjustments(self, shop, accounts, batch_size):
        """TailorOrderSecurityDepositAdjustment (refunded only) -> tailor_security_refund (out).
        Deducted adjustments are excluded — they represent internal write-offs, not cash out.
        Skip if order is deleted or converted to a booking.

        NEW source added in this rebuild version.
        """
        qs = (
            TailorOrderSecurityDepositAdjustment.objects
            .filter(order__shop=shop, adjustment_type='refunded')
            .select_related('order', 'account')
            .order_by('created_at', 'id')
        )
        def _entry(obj):
            order = obj.order
            if getattr(order, 'deleted', None) is not None:
                return None, SKIP_ORDER_DELETED
            if getattr(order, 'created_booking_id', None) is not None:
                return None, SKIP_ORDER_CONVERTED
            return self._make_entry(
                shop=shop,
                obj=obj,
                amount=obj.amount,
                direction='out',
                transaction_type='tailor_security_refund',
                reference_type='TailorOrderSecurityDepositAdjustment',
                reference_id=obj.id,
                accounts=accounts,
                transaction_datetime=obj.created_at,
            )
        return self._bulk_process(qs, _entry, batch_size, 'tailor_security_refund', shop)

    def _process_account_transfers(self, shop, accounts, batch_size):
        """AccountTransfer -> transfer_out (out) + transfer_in (in). Two entries per record.
        Both use transfer_date_time. Soft-deleted transfers are excluded.

        This processor uses a dedicated loop (not _bulk_process) because each source record
        produces two ledger entries. Stats are tracked manually in the same format.

        NEW source added in this rebuild version.
        """
        qs = (
            AccountTransfer.objects
            .filter(shop=shop, deleted__isnull=True)
            .select_related('from_account', 'to_account')
            .order_by('transfer_date_time', 'id')
        )
        stats  = _SourceStats(label='account_transfer')
        offset = 0

        while True:
            batch = list(qs[offset: offset + batch_size])
            if not batch:
                break
            offset += len(batch)

            entries = []
            for obj in batch:
                stats.scanned += 2   # two logical ledger entries per transfer record
                amount = Decimal(str(obj.amount))
                dt     = obj.transfer_date_time

                # Money Out: source account is debited
                entries.append(LedgerEntry(
                    shop=shop,
                    account=obj.from_account,
                    amount=amount,
                    signed_amount=-amount,
                    direction='out',
                    transaction_type='transfer_out',
                    payment_method='transfer',
                    reference_id=obj.id,
                    reference_type='AccountTransfer',
                    created_at=dt,
                    notes=f'transfer_out for AccountTransfer {obj.id}',
                    is_system_generated=True,
                ))
                # Money In: destination account is credited
                entries.append(LedgerEntry(
                    shop=shop,
                    account=obj.to_account,
                    amount=amount,
                    signed_amount=amount,
                    direction='in',
                    transaction_type='transfer_in',
                    payment_method='transfer',
                    reference_id=obj.id,
                    reference_type='AccountTransfer',
                    created_at=dt,
                    notes=f'transfer_in for AccountTransfer {obj.id}',
                    is_system_generated=True,
                ))
                stats.created          += 2
                stats.amount_processed += amount * 2

            if entries:
                LedgerEntry.objects.bulk_create(entries)

        if stats.created:
            self.stdout.write(f'    account_transfer: {stats.created}')
        return stats

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------

    def _bulk_process(self, qs, entry_fn, batch_size, label, shop):
        """Iterate qs in batches, build LedgerEntry objects via entry_fn, bulk_create.

        Uses offset-based slicing to avoid loading the entire queryset into memory.
        entry_fn must return (LedgerEntry, None) on success or (None, reason_str) on skip.
        Skipped records are collected into _SourceStats.skips for the summary report.
        """
        stats  = _SourceStats(label=label)
        offset = 0

        while True:
            batch = list(qs[offset: offset + batch_size])
            if not batch:
                break
            offset += len(batch)

            to_create = []
            for obj in batch:
                stats.scanned += 1
                # Derive amount for skip reporting — covers all source model conventions.
                raw_amount = (
                    getattr(obj, 'amount', None)
                    or getattr(obj, 'payment_amount', None)
                    or getattr(obj, 'refunded_amount', None)
                    or getattr(obj, 'expense', None)
                    or Decimal('0')
                )
                amount = Decimal(str(raw_amount))

                result = entry_fn(obj)
                # Normalise: entry_fn may return a bare LedgerEntry (legacy), a tuple, or None.
                if isinstance(result, tuple):
                    entry, reason = result
                elif result is None:
                    entry, reason = None, 'unknown'
                else:
                    entry, reason = result, None

                if entry is not None:
                    to_create.append(entry)
                    stats.created          += 1
                    stats.amount_processed += amount
                else:
                    stats.skipped        += 1
                    stats.amount_skipped += amount
                    stats.skips.append(_SkipRecord(
                        source=label,
                        record_id=obj.id,
                        shop_id=shop.id,
                        account_id=getattr(obj, 'account_id', None),
                        amount=amount,
                        transaction_datetime=getattr(obj, 'created_at', None),
                        reason=reason or 'unknown',
                    ))

            if to_create:
                LedgerEntry.objects.bulk_create(to_create)

        if stats.created:
            self.stdout.write(f'    {label}: {stats.created}')
        return stats

    def _make_entry(self, shop, obj, amount, direction, transaction_type,
                    reference_type, reference_id, accounts, transaction_datetime=None):
        """Build a single unsaved LedgerEntry.

        Returns:
            (LedgerEntry, None)   on success
            (None, reason_str)    when no valid account can be resolved

        Args:
            transaction_datetime: The actual business datetime. When None, falls back to
                                  obj.created_at — identical to previous behaviour for all
                                  existing callers that omit this argument.
        """
        amount = Decimal(str(amount))
        payment_method = (
            getattr(obj, 'payment_method', None)
            or getattr(obj, 'refund_method', None)
            or 'cash'
        )
        account  = self._resolve_account(obj, payment_method, accounts)
        entry_dt = transaction_datetime if transaction_datetime is not None else obj.created_at

        if account is None:
            logger.warning(
                'rebuild_ledger: skipping %s id=%s shop=%s — no valid account resolved '
                '(obj.account_id=%s, payment_method=%s)',
                reference_type, reference_id, shop.id,
                getattr(obj, 'account_id', None), payment_method,
            )
            return None, SKIP_NO_ACCOUNT

        return LedgerEntry(
            shop=shop,
            account=account,
            amount=amount,
            signed_amount=amount if direction == 'in' else -amount,
            direction=direction,
            transaction_type=transaction_type,
            payment_method=payment_method,
            reference_id=reference_id,
            reference_type=reference_type,
            created_at=entry_dt,
            notes=f'{transaction_type} for {reference_type} {reference_id}',
            is_system_generated=True,
        ), None

    def _resolve_account(self, obj, payment_method, accounts):
        """
        Account resolution order:
          1. obj.account if set, not soft-deleted, and is_active=True  -> preserves original mapping
          2. payment_method-based fallback from shop default accounts
          3. Returns None when no valid account found (caller logs and skips the entry)
        """
        existing  = getattr(obj, 'account', None)
        if existing is not None:
            deleted   = getattr(existing, 'deleted', None)
            is_active = getattr(existing, 'is_active', True)
            if deleted is None and is_active:
                return existing   # original account preserved
            logger.debug(
                'rebuild_ledger: %s id=%s — account %s unavailable '
                '(deleted=%s is_active=%s), falling back to default',
                type(obj).__name__, obj.id, existing.id, deleted, is_active,
            )
        return self._account_for_method(payment_method, accounts)

    def _account_for_method(self, payment_method, accounts):
        if payment_method == 'cash':
            return accounts.get('cash') or accounts.get('bank')
        return accounts.get('bank') or accounts.get('cash')

    def _get_or_create_accounts(self, shop, dry_run):
        """Look up existing accounts by name/type. Create only when missing."""
        qs   = Account.objects.filter(shop=shop, deleted__isnull=True)
        cash = qs.filter(name='Cash in Hand').first() or qs.filter(account_type='cash').first()
        bank = (
            qs.filter(name='Default Bank Account').first()
            or qs.filter(account_type='bank', name__icontains='default').first()
            or qs.filter(account_type='bank').first()
        )

        if not cash and not dry_run:
            cash = Account.objects.create(
                shop=shop,
                name='Cash in Hand',
                account_type='cash',
                is_default=True,
                is_active=True,
            )
            self.stdout.write(f'  Created cash account: {cash.name}')

        if not bank and not dry_run:
            bank = Account.objects.create(
                shop=shop,
                name='Default Bank Account',
                account_type='bank',
                is_default=False,
                is_active=True,
            )
            self.stdout.write(f'  Created bank account: {bank.name}')

        return {'cash': cash, 'bank': bank}

    # ------------------------------------------------------------------
    # Summary report
    # ------------------------------------------------------------------

    def _print_shop_report(self, all_stats, verbose=False):
        """Print rebuild summary.

        verbose=True  (--shop-id run): full 6-column table + per-skip detail.
        verbose=False (all-shops run): silent — grand totals only printed by handle().
        """
        if not verbose:
            return   # all-shops run: no table, no skip detail

        self.stdout.write(
            f'\n  {"SOURCE":<45} {"SCANNED":>8} {"CREATED":>8} {"SKIPPED":>8} '
            f'{"AMT CREATED":>14} {"AMT SKIPPED":>14}'
        )
        self.stdout.write(f'  {"-"*45} {"-"*8} {"-"*8} {"-"*8} {"-"*14} {"-"*14}')

        for s in all_stats:
            created_amt = f'{s.amount_processed:,.2f}' if s.created else '-'
            skipped_amt = f'{s.amount_skipped:,.2f}'   if s.skipped else '-'
            self.stdout.write(
                f'  {s.label:<45} {s.scanned:>8} {s.created:>8} {s.skipped:>8} '
                f'{created_amt:>14} {skipped_amt:>14}'
            )

        all_skips = [sk for s in all_stats for sk in s.skips]
        if all_skips:
            self.stdout.write(self.style.WARNING(
                f'\n  SKIPPED TRANSACTIONS ({len(all_skips)}):'
            ))
            for sk in all_skips:
                self.stdout.write(
                    f'    [{sk.source}] id={sk.record_id} '
                    f'amount={sk.amount} '
                    f'account_id={sk.account_id} '
                    f'dt={sk.transaction_datetime} '
                    f'reason="{sk.reason}"'
                )

        total_skipped     = sum(s.skipped for s in all_stats)
        total_amt_skipped = sum(s.amount_skipped for s in all_stats)
        if total_skipped:
            self.stdout.write(self.style.WARNING(
                f'\n  Skipped: {total_skipped} entries  |  Amount: {total_amt_skipped:,.2f}'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\n  No transactions skipped.'))

    # ------------------------------------------------------------------
    # Dry-run count helper
    # ------------------------------------------------------------------

    def _count_entries(self, shop):
        """Count how many entries would be created per table (skip logic applied)."""
        # Pre-fetch with select_related to avoid N+1 queries in count_valid().
        sales_qs      = SalesPaymentDetails.objects.filter(sale__shop=shop).select_related('sale')
        booking_qs    = PaymentDetailsV2.objects.filter(booking__shop=shop).select_related('booking')
        tailor_qs     = CustomerPayment.objects.filter(order__shop=shop).select_related('order')
        tailor_sec_qs = (
            TailorOrderSecurityPayment.objects
            .filter(order__shop=shop, is_paid=True)      # CHANGED: added is_paid=True
            .select_related('order')
        )
        sec_dep_qs = (
            SecurityDepositPayment.objects
            .filter(booking__shop=shop, is_paid=True)    # CHANGED: added is_paid=True
            .select_related('booking')
        )
        sec_adj_qs = (
            SecurityDepositAdjustment.objects
            .filter(booking__shop=shop, adjustment_type='refund')
            .select_related('booking')
        )
        refund_qs  = RefundDetails.objects.filter(booking__shop=shop).select_related('booking')
        exp_qs     = Expenses.objects.filter(shop=shop, deleted__isnull=True)
        var_exp_qs = ProductVariantExpense.objects.filter(shop=shop, deleted__isnull=True)
        # NEW querysets
        manual_income_qs  = ManualIncome.objects.filter(shop=shop, deleted__isnull=True)
        tailor_sec_adj_qs = (
            TailorOrderSecurityDepositAdjustment.objects
            .filter(order__shop=shop, adjustment_type='refunded')
            .select_related('order')
        )
        transfer_qs = AccountTransfer.objects.filter(shop=shop, deleted__isnull=True)

        def count_valid(qs, skip_fn):
            return sum(1 for obj in qs if not skip_fn(obj))

        return {
            'sale_payment':              sales_qs.count(),
            'booking_payment':           count_valid(booking_qs,      lambda o: o.booking.deleted is not None),
            'tailor_order_payment':      count_valid(tailor_qs,       lambda o: o.order.deleted is not None or o.order.created_booking_id is not None),
            'tailor_order_security_in':  count_valid(tailor_sec_qs,   lambda o: o.order.deleted is not None or o.order.created_booking_id is not None),
            'security_in':               count_valid(sec_dep_qs,      lambda o: o.booking.deleted is not None),
            'security_out':              count_valid(sec_adj_qs,      lambda o: o.booking.deleted is not None),
            'refund':                    count_valid(refund_qs,        lambda o: o.booking.deleted is not None),
            'tailor_order_refund':       count_valid(
                TailorOrderRefund.objects.filter(order__shop=shop).select_related('order'),
                lambda o: getattr(o.order, 'created_booking_id', None) is not None
            ),
            'expense (Expenses)':        exp_qs.count(),
            'expense (ProductVariant)':  var_exp_qs.count(),
            # NEW
            'manual_income':             manual_income_qs.count(),
            'tailor_security_refund':    count_valid(tailor_sec_adj_qs, lambda o: o.order.deleted is not None),
            'account_transfer (entries)':transfer_qs.count() * 2,   # 2 entries per transfer record
        }
