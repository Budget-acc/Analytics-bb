"""
Backfill TailorOrderSecurityPayment records for tailor orders that have a
security_amount but are missing that entry.

Rules:
  - All non-deleted tailor orders with security_amount > 0 are processed.
  - One entry per tailor order, created at TailorOrder.created_at.
  - Account is derived from the order's first CustomerPayment:
      cash → Cash in Hand account
      upi / gpay / bank → Default Bank account
    Fallback: cash account.
  - Entries created here have is_migration_generated=True for safe rollback.
  - Use --clear to wipe ALL existing TailorOrderSecurityPayment records for the
    target shop(s) before re-running (useful for a clean rebuild).

Usage:
  python manage.py migrate_tailor_security --dry-run
  python manage.py migrate_tailor_security --shop-id 115 --dry-run
  python manage.py migrate_tailor_security --shop-id 115 --force
  python manage.py migrate_tailor_security --shop-id 115 --rollback --force
  python manage.py migrate_tailor_security --shop-id 115 --clear --force
  python manage.py migrate_tailor_security --force
"""

import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from finance.models import Account
from shop.models import Shop
from tailored.models import TailorOrder, TailorOrderSecurityPayment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Backfill TailorOrderSecurityPayment for non-converted tailor orders '
        'that have a security_amount but are missing that record.'
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
            '--clear',
            action='store_true',
            help='Delete ALL existing TailorOrderSecurityPayment entries for the target shop(s) before migrating',
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
        clear    = options['clear']
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
            if rollback:
                action = 'delete all is_migration_generated=True TailorOrderSecurityPayment entries'
            elif clear:
                action = 'delete ALL TailorOrderSecurityPayment entries and rebuild from scratch'
            else:
                action = 'create missing TailorOrderSecurityPayment records'
            self.stdout.write(self.style.WARNING(
                f'This will {action} for {total_shops} shop(s).'
            ))
            confirm = input('Proceed? (yes/no): ').strip().lower()
            if confirm not in ('yes', 'y'):
                self.stdout.write(self.style.ERROR('Cancelled.'))
                return

        total_created = 0
        total_deleted = 0

        for shop in shops:
            self.stdout.write(f'\nShop: {shop.name} (ID: {shop.id})')

            if rollback:
                total_deleted += self._rollback_shop(shop, dry_run)
            else:
                if clear and not dry_run:
                    total_deleted += self._clear_shop(shop)
                total_created += self._migrate_shop(shop, dry_run)

        self.stdout.write('')
        if rollback:
            self.stdout.write(self.style.SUCCESS(
                f'Rollback complete — entries deleted: {total_deleted}'
            ))
        elif clear:
            self.stdout.write(self.style.SUCCESS(
                f'Clear + migrate complete — deleted: {total_deleted}, created: {total_created}'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Migration complete — entries created: {total_created}'
            ))

    # ------------------------------------------------------------------
    # Per-shop clear
    # ------------------------------------------------------------------

    def _clear_shop(self, shop):
        deleted, _ = TailorOrderSecurityPayment.objects.filter(
            order__shop=shop,
        ).delete()
        self.stdout.write(f'  Cleared {deleted} existing TailorOrderSecurityPayment record(s)')
        return deleted

    # ------------------------------------------------------------------
    # Per-shop rollback
    # ------------------------------------------------------------------

    def _rollback_shop(self, shop, dry_run):
        qs = TailorOrderSecurityPayment.objects.filter(
            order__shop=shop,
            is_migration_generated=True,
        )
        count = qs.count()
        if dry_run:
            self.stdout.write(f'  Would delete {count} migration-generated record(s)')
            return 0
        qs.delete()
        self.stdout.write(f'  Deleted {count} migration-generated record(s)')
        return count

    # ------------------------------------------------------------------
    # Per-shop migration
    # ------------------------------------------------------------------

    def _migrate_shop(self, shop, dry_run):
        orders = TailorOrder.objects.filter(
            shop=shop,
            security_amount__gt=0,
        ).prefetch_related('security_payments', 'payments')

        shop_accounts = self._get_shop_accounts(shop)
        if not shop_accounts['cash'] and not shop_accounts['bank']:
            self.stdout.write(self.style.WARNING('  No accounts found for this shop — skipping'))
            return 0

        to_create = []

        for order in orders:
            if order.security_payments.exists():
                continue  # Already has a security payment record

            amount = Decimal(str(order.security_amount))
            account, payment_method = self._resolve_account(order, shop_accounts)

            to_create.append(TailorOrderSecurityPayment(
                order=order,
                amount=amount,
                account=account,
                created_at=order.created_at,
                is_migration_generated=True,
            ))

        if dry_run:
            if to_create:
                self.stdout.write(f'  Would create {len(to_create)} TailorOrderSecurityPayment record(s)')
            else:
                self.stdout.write('  Nothing to create')
            return 0

        if not to_create:
            self.stdout.write('  Nothing to create')
            return 0

        with transaction.atomic():
            TailorOrderSecurityPayment.objects.bulk_create(to_create)

        self.stdout.write(f'  Created {len(to_create)} TailorOrderSecurityPayment record(s)')
        return len(to_create)

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
          cash              → Cash in Hand (account_type='cash')
          upi / gpay / bank → Default Bank Account (account_type='bank')
        Falls back to whichever account is available if the preferred one is missing.
        """
        if payment_method == 'cash':
            return shop_accounts.get('cash') or shop_accounts.get('bank')
        return shop_accounts.get('bank') or shop_accounts.get('cash')

    def _resolve_account(self, order, shop_accounts):
        """
        Derive account for the security payment.

        Priority:
          1. Existing TailorOrderSecurityPayment account (if already set on another record)
          2. First CustomerPayment for this order → use its payment_method to pick account
          3. Fallback: cash account
        """
        # 1. Existing security payment with account
        existing = order.security_payments.filter(account__isnull=False).first()
        if existing and existing.account and existing.account.deleted is None:
            method = 'cash'  # TailorOrderSecurityPayment has no payment_method field
            return existing.account, method

        # 2. Derive from first CustomerPayment
        first_cp = order.payments.order_by('created_at', 'id').first()
        if first_cp:
            method = getattr(first_cp, 'payment_method', None) or 'cash'
            account = self._account_for_method(method, shop_accounts)
            return account, method

        # 3. Fallback
        return shop_accounts.get('cash') or shop_accounts.get('bank'), 'cash'
