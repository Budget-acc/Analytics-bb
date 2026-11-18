"""
Backfill SecurityDepositPayment and SecurityDepositAdjustment (refund) records
for completed bookings that have a security_amount but are missing those entries.

Cases handled (per completed booking with security_amount > 0):
  - No SecurityDepositPayment  → create one at booking.created_at
  - No SecurityDepositAdjustment (any type) → create a full refund at booking.completed_at

Entries created by this command have is_migration_generated=True so they can be
identified and cleanly rolled back if corrections are needed.

Usage:
  python manage.py migrate_booking_security --dry-run
  python manage.py migrate_booking_security --shop-id 115 --dry-run
  python manage.py migrate_booking_security --shop-id 115 --force
  python manage.py migrate_booking_security --shop-id 115 --rollback --force
  python manage.py migrate_booking_security --force
"""

import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from booking_v2.models import (
    BookingV2,
    SecurityDepositAdjustment,
    SecurityDepositPayment,
)
from finance.models import Account
from shop.models import Shop

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Backfill SecurityDepositPayment and SecurityDepositAdjustment (refund) '
        'for completed bookings that are missing these records.'
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
            '--rollback',
            action='store_true',
            help='Delete all is_migration_generated=True entries for the target shop(s)',
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
        shop_id  = options['shop_id']
        dry_run  = options['dry_run']
        rollback = options['rollback']
        force    = options['force']

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
            action = 'rollback migration-generated security entries' if rollback else 'create missing security deposit/refund records'
            self.stdout.write(self.style.WARNING(
                f'This will {action} for {total_shops} shop(s).'
            ))
            confirm = input('Proceed? (yes/no): ').strip().lower()
            if confirm not in ('yes', 'y'):
                self.stdout.write(self.style.ERROR('Cancelled.'))
                return

        total_deposits_created  = 0
        total_refunds_created   = 0
        total_deposits_deleted  = 0
        total_refunds_deleted   = 0

        for shop in shops:
            self.stdout.write(f'\nShop: {shop.name} (ID: {shop.id})')

            if rollback:
                d, r = self._rollback_shop(shop, dry_run)
                total_deposits_deleted += d
                total_refunds_deleted  += r
            else:
                d, r = self._migrate_shop(shop, dry_run)
                total_deposits_created += d
                total_refunds_created  += r

        self.stdout.write('')
        if rollback:
            self.stdout.write(self.style.SUCCESS(
                f'Rollback complete — deposits deleted: {total_deposits_deleted}, '
                f'refunds deleted: {total_refunds_deleted}'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Migration complete — deposits created: {total_deposits_created}, '
                f'refunds created: {total_refunds_created}'
            ))

    # ------------------------------------------------------------------
    # Per-shop migration
    # ------------------------------------------------------------------

    def _migrate_shop(self, shop, dry_run):
        bookings = BookingV2.objects.filter(
            shop=shop,
            booking_status__in=('completed', 'upcoming'),
            security_amount__gt=0,
            deleted__isnull=True,
        ).prefetch_related('security_deposit_payments', 'security_adjustments', 'payments')

        shop_accounts = self._get_shop_accounts(shop)
        if not shop_accounts['cash'] and not shop_accounts['bank']:
            self.stdout.write(self.style.WARNING('  No accounts found for this shop — skipping'))
            return 0, 0

        deposits_to_create = []
        refunds_to_create  = []

        for booking in bookings:
            amount = Decimal(str(booking.security_amount))

            has_payment    = booking.security_deposit_payments.exists()
            has_adjustment = booking.security_adjustments.exists()

            # Account and payment_method resolution (shared for both entries)
            account, payment_method = self._resolve_account(booking, shop_accounts)

            # Deposit entry — applies to both upcoming and completed bookings
            if not has_payment:
                deposits_to_create.append(SecurityDepositPayment(
                    booking=booking,
                    amount=amount,
                    payment_method=payment_method,
                    account=account,
                    created_at=booking.created_at,
                    is_migration_generated=True,
                ))

            # Refund entry — only for completed bookings with no adjustment yet
            if booking.booking_status == 'completed' and not has_adjustment:
                refund_ts = booking.completed_at or booking.return_date
                if not refund_ts:
                    self.stdout.write(self.style.WARNING(
                        f'  Booking {booking.id}: no completed_at or return_date — skipping refund'
                    ))
                    continue

                refunds_to_create.append(SecurityDepositAdjustment(
                    booking=booking,
                    adjustment_type='refund',
                    amount=amount,
                    payment_method=payment_method,
                    account=account,
                    reason='Security refund for completed booking (backfilled by migration)',
                    created_at=refund_ts,
                    is_migration_generated=True,
                ))

        if dry_run:
            if deposits_to_create:
                self.stdout.write(f'  Would create {len(deposits_to_create)} SecurityDepositPayment record(s)')
            if refunds_to_create:
                self.stdout.write(f'  Would create {len(refunds_to_create)} SecurityDepositAdjustment (refund) record(s)')
            return 0, 0

        deposits_created = 0
        refunds_created  = 0

        with transaction.atomic():
            if deposits_to_create:
                SecurityDepositPayment.objects.bulk_create(deposits_to_create)
                deposits_created = len(deposits_to_create)
                self.stdout.write(f'  Created {deposits_created} deposit record(s)')

            if refunds_to_create:
                SecurityDepositAdjustment.objects.bulk_create(refunds_to_create)
                refunds_created = len(refunds_to_create)
                self.stdout.write(f'  Created {refunds_created} refund record(s)')

        return deposits_created, refunds_created

    # ------------------------------------------------------------------
    # Per-shop rollback
    # ------------------------------------------------------------------

    def _rollback_shop(self, shop, dry_run):
        deposit_qs = SecurityDepositPayment.objects.filter(
            booking__shop=shop,
            is_migration_generated=True,
        )
        refund_qs = SecurityDepositAdjustment.objects.filter(
            booking__shop=shop,
            is_migration_generated=True,
        )

        deposit_count = deposit_qs.count()
        refund_count  = refund_qs.count()

        if dry_run:
            self.stdout.write(f'  Would delete {deposit_count} deposit(s) and {refund_count} refund(s)')
            return 0, 0

        with transaction.atomic():
            deposit_qs.delete()
            refund_qs.delete()

        self.stdout.write(f'  Deleted {deposit_count} deposit(s) and {refund_count} refund(s)')
        return deposit_count, refund_count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_shop_accounts(self, shop):
        """Return {'cash': <Account|None>, 'bank': <Account|None>} for the shop."""
        qs = Account.objects.filter(shop=shop, deleted__isnull=True, is_active=True)
        return {
            'cash': qs.filter(account_type='cash').first(),
            'bank': qs.filter(account_type='bank').first(),
        }

    def _account_for_method(self, payment_method, shop_accounts):
        """
        Map payment method to the correct shop account.
          cash              → cash account (Cash in Hand)
          upi / gpay / bank → bank account (Default Bank Account)
        Falls back to whichever account is available if the preferred one is missing.
        """
        if payment_method == 'cash':
            return shop_accounts.get('cash') or shop_accounts.get('bank')
        return shop_accounts.get('bank') or shop_accounts.get('cash')

    def _resolve_account(self, booking, shop_accounts):
        """
        Derive payment_method and account for a booking's security entry.

        Priority:
          1. Existing SecurityDepositPayment for this booking (already has method + account)
          2. First advance payment on the booking → use its payment_method to pick account
          3. Fallback: payment_method='cash', account=cash account
        """
        # 1. Use existing security deposit payment if one already exists
        existing = booking.security_deposit_payments.filter(
            account__isnull=False,
        ).first()
        if existing and existing.account and existing.account.deleted is None:
            method = existing.payment_method or 'cash'
            return existing.account, method

        # 2. Derive from the first advance/booking payment
        advance = booking.payments.filter(
            account__isnull=False,
            account__deleted__isnull=True,
        ).order_by('created_at', 'id').first()
        if advance:
            method = getattr(advance, 'payment_method', None) or 'cash'
            # Always map method → correct account type rather than reusing the
            # advance payment's account directly (security may use a different account).
            account = self._account_for_method(method, shop_accounts)
            return account, method

        # 3. Fallback to cash
        return shop_accounts.get('cash') or shop_accounts.get('bank'), 'cash'
