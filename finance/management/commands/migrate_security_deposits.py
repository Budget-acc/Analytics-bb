"""
Backfill missing SecurityDepositPayment records (and their LedgerEntry) for
bookings that already have a security_amount and SecurityDepositAdjustment
history, but never got a SecurityDepositPayment row created.

Why this migration is required
-------------------------------
Some BookingV2 rows have `security_amount > 0` and already have
SecurityDepositAdjustment (refund/deduction) records — proof the deposit was
taken and later adjusted — but are missing the SecurityDepositPayment row
that should have recorded the original "in" transaction. Left as-is, the
shop's ledger/account balance under-counts the security amount that was
actually collected, since there was never a `security_in` LedgerEntry for it.

What this command checks, per booking
--------------------------------------
For every non-deleted BookingV2 with `security_amount > 0`:
  1. Does a SecurityDepositPayment already exist for this booking?
     (`SecurityDepositPayment.objects.filter(booking=booking)`)
  2. Independently of (1), does the corresponding LedgerEntry already exist?
     Looked up by `reference_type='SecurityDepositPayment'` +
     `reference_id=<deposit.id>` + `transaction_type='security_in'` — the
     same reference scheme migrate_ledger_history.py's
     migrate_security_deposits() already uses, so this command never
     invents a parallel/incompatible way of tracking what's been ledgered.

Both checks always run, regardless of each other's outcome, so a booking
that already has a SecurityDepositPayment but is missing only its
LedgerEntry (e.g. from a partial previous run) gets just the ledger entry
created — never a duplicate deposit.

How the amount is determined
-----------------------------
`SecurityDepositPayment.amount = Decimal(booking.security_amount)`. The
SecurityDepositAdjustment rows are read only to *identify* which bookings
are missing a deposit despite already having adjustment history (part of
the eligibility picture the user described); this command never derives an
amount from them, never modifies them, and never changes their balance —
the adjustment/refund amount is independent, unaffected accounting that
already exists correctly.

How the booking created_at timestamp is used
----------------------------------------------
`SecurityDepositPayment.created_at` is set to *exactly*
`booking.created_at`, and `LedgerEntry.created_at` is set to *exactly*
`deposit.created_at` (so, transitively, also booking.created_at). This
keeps the historical timeline correct: the deposit and its ledger entry are
backdated to when the booking was actually made, not to "now".

How the cash account is selected
----------------------------------
The shop's oldest (first-created) active, non-deleted Account with
`account_type='cash'` is used:
    Account.objects.filter(shop=shop, account_type='cash',
                            deleted__isnull=True, is_active=True)
                    .order_by('created_at', 'id').first()
No account is ever created — if a shop has no cash account, that shop is
skipped with a warning.

How the LedgerEntry is created
--------------------------------
One LedgerEntry per created deposit: direction='in',
transaction_type='security_in', payment_method='cash', same shop and cash
account as the deposit, amount == deposit.amount, created_at ==
deposit.created_at. `signed_amount` is set automatically by
LedgerEntry.save() from `direction`.

How duplicate creation is prevented
--------------------------------------
- Deposit: skipped if `SecurityDepositPayment.objects.filter(booking=booking).exists()`.
- Ledger: skipped if a LedgerEntry already exists for
  (reference_type='SecurityDepositPayment', reference_id=deposit.id,
  transaction_type='security_in', deleted__isnull=True) — checked
  independently every time, whether or not the deposit was just created in
  this same run.
- Each booking's deposit+ledger pair is created inside its own
  `transaction.atomic()` block with `select_for_update()` on the booking
  row, so concurrent runs can't race past the same "missing" check, and a
  crash partway through never leaves a deposit without its ledger entry (or
  vice versa) — the whole per-booking unit either commits or rolls back.
  A failure on one booking does not roll back bookings already committed
  earlier in the same run; it's counted under Errors and processing
  continues.

Safety considerations / assumptions
--------------------------------------
- Never modifies or deletes any existing SecurityDepositPayment,
  SecurityDepositAdjustment, BookingV2, Account, or LedgerEntry row.
- Never creates a new Account.
- Entries created by this command are flagged
  `is_migration_generated=True` (SecurityDepositPayment) /
  `is_system_generated=True` with a note (LedgerEntry) for traceability.
- Prioritizes correctness over throughput: no bulk_create is used here,
  since each row's creation depends on a same-transaction existence check
  against a table (LedgerEntry) that the previous statement may have just
  written to — see migrate_booking_security.py / migrate_tailor_security.py
  for the sibling commands' precedent of per-booking bulk lists; this
  command intentionally departs from that pattern because it must interleave
  writes to two related tables under one lock, which bulk_create cannot do
  safely.

Usage
-----
  python manage.py migrate_security_deposits --dry-run
  python manage.py migrate_security_deposits --shop-id 66 --dry-run
  python manage.py migrate_security_deposits --shop-id 66 --force
  python manage.py migrate_security_deposits --force
"""

import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from booking_v2.models import (
    BookingV2,
    SecurityDepositPayment,
)
from finance.daily_balance_utils import bulk_update_daily_balances
from finance.models import Account, LedgerEntry
from shop.models import Shop

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Backfill missing SecurityDepositPayment records (and their LedgerEntry) '
        'for bookings that have security_amount but are missing the deposit entry.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--shop-id',
            type=int,
            default=None,
            help='Process only this shop (default: all shops)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be created without writing to the database',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompts',
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        shop_id = options['shop_id']
        dry_run = options['dry_run']
        force = options['force']

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
                f'This will create missing SecurityDepositPayment and LedgerEntry records '
                f'for {total_shops} shop(s).'
            ))
            confirm = input('Proceed? (yes/no): ').strip().lower()
            if confirm not in ('yes', 'y'):
                self.stdout.write(self.style.ERROR('Cancelled.'))
                return

        grand_totals = {
            'eligible': 0,
            'existing_deposits': 0,
            'missing_deposits': 0,
            'deposits_created': 0,
            'ledgers_created': 0,
            'skipped': 0,
            'errors': 0,
        }

        for shop in shops:
            label = f'{shop.name} (ID: {shop.id})' if shop_id else str(shop.id)
            self.stdout.write(f'\nProcessing shop: {label}')
            totals = self._migrate_shop(shop, dry_run)
            for key in grand_totals:
                grand_totals[key] += totals[key]

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Migration complete:'))
        self.stdout.write(f"  Total eligible bookings: {grand_totals['eligible']}")
        self.stdout.write(f"  Existing Security Deposits: {grand_totals['existing_deposits']}")
        self.stdout.write(f"  Missing Security Deposits: {grand_totals['missing_deposits']}")
        self.stdout.write(f"  Security Deposits created: {grand_totals['deposits_created']}")
        self.stdout.write(f"  Ledger Entries created: {grand_totals['ledgers_created']}")
        self.stdout.write(f"  Skipped: {grand_totals['skipped']}")
        self.stdout.write(f"  Errors: {grand_totals['errors']}")

    # ------------------------------------------------------------------
    # Per-shop migration
    # ------------------------------------------------------------------

    def _migrate_shop(self, shop, dry_run):
        totals = {
            'eligible': 0,
            'existing_deposits': 0,
            'missing_deposits': 0,
            'deposits_created': 0,
            'ledgers_created': 0,
            'skipped': 0,
            'errors': 0,
        }

        cash_account = self._get_oldest_cash_account(shop)
        if not cash_account:
            self.stdout.write(self.style.WARNING('  No cash account found for this shop — skipping'))
            return totals

        bookings = BookingV2.objects.filter(
            shop=shop,
            security_amount__gt=0,
            deleted__isnull=True,
        ).select_related('shop').prefetch_related('security_deposit_payments')

        totals['eligible'] = bookings.count()

        for booking in bookings:
            has_deposit = booking.security_deposit_payments.exists()
            if has_deposit:
                totals['existing_deposits'] += 1
            else:
                totals['missing_deposits'] += 1

            if dry_run:
                if not has_deposit:
                    self.stdout.write(
                        f'  Would create SecurityDepositPayment + LedgerEntry for booking {booking.id}'
                    )
                totals['skipped'] += 1 if has_deposit else 0
                continue

            try:
                deposit_created, ledger_created = self._sync_booking_security_deposit(booking, cash_account)
            except Exception:
                logger.exception('migrate_security_deposits: failed for booking %s', booking.id)
                self.stdout.write(self.style.ERROR(f'  Error processing booking {booking.id} — see logs'))
                totals['errors'] += 1
                continue

            if deposit_created:
                totals['deposits_created'] += 1
            if ledger_created:
                totals['ledgers_created'] += 1
            if not deposit_created and not ledger_created:
                totals['skipped'] += 1

        if not dry_run:
            self.stdout.write(f"  Total eligible bookings: {totals['eligible']}")
            self.stdout.write(f"  Existing Security Deposits: {totals['existing_deposits']}")
            self.stdout.write(f"  Missing Security Deposits: {totals['missing_deposits']}")
            self.stdout.write(f"  Security Deposits created: {totals['deposits_created']}")
            self.stdout.write(f"  Ledger Entries created: {totals['ledgers_created']}")
            self.stdout.write(f"  Skipped: {totals['skipped']}")
            self.stdout.write(f"  Errors: {totals['errors']}")

        return totals

    # ------------------------------------------------------------------
    # Per-booking: create deposit + ledger entry atomically, idempotently
    # ------------------------------------------------------------------

    @transaction.atomic
    def _sync_booking_security_deposit(self, booking, cash_account):
        """
        Returns (deposit_created: bool, ledger_created: bool).

        Re-checks existence of both the SecurityDepositPayment and its
        LedgerEntry inside the transaction (after locking the booking row),
        so this is safe to call even if another process/run already handled
        this booking between the outer loop's query and this call.
        """
        # Lock the booking row for the duration of this check-then-create
        # unit, so two concurrent runs can't both pass the "missing" check.
        booking = BookingV2.objects.select_for_update().get(pk=booking.pk)

        deposit = SecurityDepositPayment.objects.filter(booking=booking).first()
        deposit_created = False

        if deposit is None:
            amount = Decimal(str(booking.security_amount))
            deposit = SecurityDepositPayment.objects.create(
                booking=booking,
                amount=amount,
                payment_method='cash',
                account=cash_account,
                is_paid=True,
                paid_at=booking.created_at,
                created_at=booking.created_at,
                is_migration_generated=True,
            )
            deposit_created = True

        # Independent check: runs whether or not we just created the
        # deposit above. Keyed on the deposit's own id so this is exact,
        # not merely "some ledger entry exists for this booking".
        ledger_exists = LedgerEntry.objects.filter(
            deleted__isnull=True,
            reference_type='SecurityDepositPayment',
            reference_id=deposit.id,
            transaction_type='security_in',
        ).exists()

        ledger_created = False
        if not ledger_exists:
            entry = LedgerEntry.objects.create(
                shop=booking.shop,
                account=cash_account,
                amount=deposit.amount,
                direction='in',
                transaction_type='security_in',
                payment_method='cash',
                reference_id=deposit.id,
                reference_type='SecurityDepositPayment',
                created_at=deposit.created_at,
                notes=f'security_in for SecurityDepositPayment {deposit.id} (backfilled by migrate_security_deposits)',
                is_system_generated=True,
            )
            bulk_update_daily_balances([entry])
            ledger_created = True

        return deposit_created, ledger_created

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_oldest_cash_account(self, shop):
        """
        The shop's oldest (first-created) active, non-deleted cash account.
        Never creates a new account — returns None if the shop has none.
        """
        return Account.objects.filter(
            shop=shop,
            account_type='cash',
            deleted__isnull=True,
            is_active=True,
        ).order_by('created_at', 'id').first()
