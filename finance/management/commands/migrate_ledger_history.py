import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from shop.models import Shop
from finance.models import Account, LedgerEntry
from finance.daily_balance_utils import bulk_update_daily_balances
from booking_v2.models import BookingV2, PaymentDetailsV2, RefundDetails, SecurityDepositPayment, SecurityDepositAdjustment
from sales.models import Sale, SalesPaymentDetails
from tailored.models import TailorOrder, CustomerPayment, TailorOrderSecurityPayment
from expenses_v2.models import Expenses, ProductVariantExpense

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Backfill LedgerEntry from historical data and create default accounts for shops'

    def add_arguments(self, parser):
        parser.add_argument(
            '--shop-id',
            type=int,
            default=None,
            help='Specific shop ID to migrate (default: all shops)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to process per batch (default: 1000)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to database'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompts'
        )

    def handle(self, *args, **options):
        shop_id = options['shop_id']
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        force = options['force']

        # Get shops to process
        if shop_id:
            shops = Shop.objects.filter(id=shop_id)
            if not shops.exists():
                self.stdout.write(self.style.ERROR(f'Shop with ID {shop_id} not found'))
                return
        else:
            shops = Shop.objects.all()

        total_shops = shops.count()
        self.stdout.write(f'Processing {total_shops} shop(s)...')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No database changes will be made'))

        total_accounts_created = 0
        total_ledger_created = 0
        total_skipped = 0
        total_accounts_updated = 0

        for shop in shops:
            self.stdout.write(f'\nProcessing shop: {shop.name} (ID: {shop.id})')

            # Step 1: Create default accounts
            accounts_created, cash_account, bank_account = self.create_default_accounts(shop, dry_run)
            total_accounts_created += accounts_created

            # Step 2: Backfill ledger entries
            ledger_created, skipped, accounts_updated = self.backfill_ledger(shop, cash_account, bank_account, batch_size, dry_run)
            total_ledger_created += ledger_created
            total_skipped += skipped
            total_accounts_updated += accounts_updated

        # Summary
        self.stdout.write(self.style.SUCCESS(f'\nMigration complete:'))
        self.stdout.write(f'  Accounts created: {total_accounts_created}')
        self.stdout.write(f'  Ledger entries created: {total_ledger_created}')
        self.stdout.write(f'  Entries skipped (already exist): {total_skipped}')
        self.stdout.write(f'  Payment records backfilled with account_id: {total_accounts_updated}')

    def create_default_accounts(self, shop, dry_run):
        """Create default Cash and Bank accounts if they don't exist"""
        accounts_created = 0

        # Cash account
        cash_account = Account.objects.filter(shop=shop, name='Cash in Hand').first()
        if not cash_account:
            if dry_run:
                self.stdout.write(f'  Would create cash account: Cash in Hand')
                cash_account = Account(shop=shop, name='Cash in Hand', account_type='cash', is_default=True, is_active=True)
            else:
                cash_account = Account.objects.create(
                    shop=shop,
                    name='Cash in Hand',
                    account_type='cash',
                    is_default=True,
                    is_active=True
                )
                accounts_created += 1
                self.stdout.write(f'  Created cash account: {cash_account.name}')

        # Bank account
        bank_account = Account.objects.filter(shop=shop, name='Default Bank Account').first()
        if not bank_account:
            if dry_run:
                self.stdout.write(f'  Would create bank account: Default Bank Account')
                bank_account = Account(shop=shop, name='Default Bank Account', account_type='bank', is_default=False, is_active=True)
            else:
                bank_account = Account.objects.create(
                    shop=shop,
                    name='Default Bank Account',
                    account_type='bank',
                    is_default=False,
                    is_active=True
                )
                accounts_created += 1
                self.stdout.write(f'  Created bank account: {bank_account.name}')

        return accounts_created, cash_account, bank_account

    def get_account_for_payment_method(self, payment_method, accounts):
        """Map payment method to appropriate account: cash->cash, gpay/upi/bank->bank"""
        if payment_method == 'cash':
            return accounts['cash']
        else:  # gpay, upi, bank -> bank account
            return accounts['bank']

    def backfill_ledger(self, shop, cash_account, bank_account, batch_size, dry_run):
        """Backfill ledger entries from historical data"""
        ledger_created = 0
        skipped = 0
        accounts_updated = 0

        if not cash_account or not bank_account:
            self.stdout.write(self.style.ERROR('  Accounts not available, skipping ledger backfill'))
            return 0, 0, 0

        accounts = {'cash': cash_account, 'bank': bank_account}

        # Migrate sales payments
        created, skip, updated = self.migrate_sales_payments(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Migrate booking payments
        created, skip, updated = self.migrate_booking_payments(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Migrate tailor payments (only non-converted)
        created, skip, updated = self.migrate_tailor_payments(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Migrate tailor security payments (only non-converted)
        created, skip, updated = self.migrate_tailor_security_payments(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Migrate security deposits
        created, skip, updated = self.migrate_security_deposits(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Migrate security adjustments (refunds only)
        created, skip, updated = self.migrate_security_adjustments(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Migrate refunds
        created, skip, updated = self.migrate_refunds(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Migrate expenses
        created, skip, updated = self.migrate_expenses(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip
        accounts_updated += updated

        # Handle completed bookings with no security payments
        created, skip = self.handle_completed_bookings_security(shop, accounts, batch_size, dry_run)
        ledger_created += created
        skipped += skip

        return ledger_created, skipped, accounts_updated

    def migrate_sales_payments(self, shop, accounts, batch_size, dry_run):
        """Migrate SalesPaymentDetails to LedgerEntry — only unmigrated records (account__isnull=True)"""
        qs = SalesPaymentDetails.objects.filter(
            sale__shop=shop, account__isnull=True
        ).select_related('sale', 'account')
        return self._bulk_create_ledger_entries(
            qs, 'SalesPaymentDetails', 'sale_payment', 'in', accounts, batch_size, dry_run
        )

    def migrate_booking_payments(self, shop, accounts, batch_size, dry_run):
        """Migrate PaymentDetailsV2 to LedgerEntry — only unmigrated records (account__isnull=True)"""
        qs = PaymentDetailsV2.objects.filter(
            booking__shop=shop, account__isnull=True
        ).select_related('booking', 'account')
        return self._bulk_create_ledger_entries(
            qs, 'PaymentDetailsV2', 'booking_payment', 'in', accounts, batch_size, dry_run
        )

    def migrate_tailor_payments(self, shop, accounts, batch_size, dry_run):
        """Migrate CustomerPayment for non-converted TailorOrders — only unmigrated records (account__isnull=True)"""
        qs = CustomerPayment.objects.filter(
            order__shop=shop, order__created_booking__isnull=True, account__isnull=True
        ).select_related('order', 'account')
        return self._bulk_create_ledger_entries(
            qs, 'CustomerPayment', 'tailor_order_payment', 'in', accounts, batch_size, dry_run
        )

    def migrate_tailor_security_payments(self, shop, accounts, batch_size, dry_run):
        """Backfill TailorOrderSecurityPayment + ledger for non-converted TailorOrders.

        Historical data may not have TailorOrderSecurityPayment rows. For those orders:
        1. Read security_amount from TailorOrder.
        2. Derive payment_method from first CustomerPayment for the order.
        3. Use that payment_method to resolve account when account is missing.

        Ledger ownership for tailor security always uses:
        - reference_type='TailorOrderSecurity'
        - reference_id=<tailor_order_id>
        """
        qs = TailorOrder.objects.filter(
            shop=shop,
            created_booking__isnull=True,
            security_amount__gt=0,
        ).order_by('id')

        created = 0
        skipped = 0
        accounts_updated = 0

        while qs.exists():
            batch = list(qs[:batch_size])
            ledger_entries = []
            security_to_create = []
            security_account_updates = {}

            order_ids = [order.id for order in batch]
            existing_order_ids = set(
                LedgerEntry.objects.filter(
                    deleted__isnull=True,
                    reference_type='TailorOrderSecurity',
                    reference_id__in=order_ids,
                    transaction_type='tailor_order_security_in',
                ).values_list('reference_id', flat=True)
            )

            for order in batch:
                # First available customer payment method drives security payment method.
                first_customer_payment = CustomerPayment.objects.filter(order_id=order.id).order_by('created_at', 'id').first()
                derived_payment_method = getattr(first_customer_payment, 'payment_method', None) or 'cash'

                existing_security_payment = TailorOrderSecurityPayment.objects.filter(order_id=order.id).order_by('created_at', 'id').first()

                # Resolve a valid (non-deleted) account from first_customer_payment.
                _raw_cp_account = getattr(first_customer_payment, 'account', None)
                _valid_cp_account = (
                    _raw_cp_account
                    if (_raw_cp_account and _raw_cp_account.deleted is None)
                    else None
                )

                if existing_security_payment:
                    security_account = existing_security_payment.account
                    if not security_account:
                        security_account = _valid_cp_account or self.get_account_for_payment_method(
                            derived_payment_method,
                            accounts,
                        )
                        key = security_account.id
                        security_account_updates.setdefault(key, []).append(existing_security_payment.id)
                else:
                    security_account = _valid_cp_account or self.get_account_for_payment_method(
                        derived_payment_method,
                        accounts,
                    )

                    if not dry_run:
                        security_to_create.append(
                            TailorOrderSecurityPayment(
                                order=order,
                                amount=order.security_amount,
                                account=security_account,
                                created_at=order.created_at or timezone.now(),
                            )
                        )

                if order.id in existing_order_ids:
                    skipped += 1
                    continue

                account = security_account or accounts['cash']
                payment_method = derived_payment_method
                created_at = order.created_at or timezone.now()

                ledger_entries.append(
                    LedgerEntry(
                        shop=order.shop,
                        account=account,
                        amount=order.security_amount,
                        signed_amount=order.security_amount,
                        direction='in',
                        transaction_type='tailor_order_security_in',
                        payment_method=payment_method,
                        reference_id=order.id,
                        reference_type='TailorOrderSecurity',
                        created_at=created_at,
                        notes=f'tailor_order_security_in for TailorOrderSecurity {order.id}',
                        is_system_generated=True,
                    )
                )

            if not dry_run:
                if ledger_entries or security_account_updates or security_to_create:
                    with transaction.atomic():
                        if security_to_create:
                            TailorOrderSecurityPayment.objects.bulk_create(security_to_create)

                        if security_account_updates:
                            for account_id, payment_ids in security_account_updates.items():
                                TailorOrderSecurityPayment.objects.filter(id__in=payment_ids).update(account_id=account_id)
                                accounts_updated += len(payment_ids)

                        if ledger_entries:
                            LedgerEntry.objects.bulk_create(ledger_entries)
                            created += len(ledger_entries)
                            bulk_update_daily_balances(ledger_entries)
            elif ledger_entries:
                self.stdout.write(f'  Would create {len(ledger_entries)} tailor_order_security_in entries')
                would_create_security = len([o for o in batch if not TailorOrderSecurityPayment.objects.filter(order_id=o.id).exists()])
                if would_create_security:
                    self.stdout.write(f'  Would create {would_create_security} TailorOrderSecurityPayment records')

                would_update_security = sum(len(v) for v in security_account_updates.values())
                if would_update_security:
                    self.stdout.write(f'  Would update account_id on {would_update_security} TailorOrderSecurityPayment records')

            ids = [order.id for order in batch]
            qs = qs.exclude(id__in=ids)

        return created, skipped, accounts_updated

    def migrate_security_deposits(self, shop, accounts, batch_size, dry_run):
        """Migrate SecurityDepositPayment to LedgerEntry — only unmigrated records (account__isnull=True)"""
        qs = SecurityDepositPayment.objects.filter(
            booking__shop=shop, account__isnull=True
        ).select_related('booking', 'account')
        return self._bulk_create_ledger_entries(
            qs, 'SecurityDepositPayment', 'security_in', 'in', accounts, batch_size, dry_run
        )

    def migrate_security_adjustments(self, shop, accounts, batch_size, dry_run):
        """Migrate SecurityDepositAdjustment refunds to LedgerEntry — only unmigrated records (account__isnull=True)"""
        qs = SecurityDepositAdjustment.objects.filter(
            booking__shop=shop, adjustment_type='refund', account__isnull=True
        ).select_related('booking', 'account')
        return self._bulk_create_ledger_entries(
            qs, 'SecurityDepositAdjustment', 'security_out', 'out', accounts, batch_size, dry_run
        )

    def migrate_refunds(self, shop, accounts, batch_size, dry_run):
        """Migrate RefundDetails to LedgerEntry — only unmigrated records (account__isnull=True)"""
        qs = RefundDetails.objects.filter(
            booking__shop=shop, account__isnull=True
        ).select_related('booking', 'account')
        return self._bulk_create_ledger_entries(
            qs, 'RefundDetails', 'refund', 'out', accounts, batch_size, dry_run
        )

    def migrate_expenses(self, shop, accounts, batch_size, dry_run):
        """Migrate Expenses and ProductVariantExpense to LedgerEntry — only unmigrated records (account__isnull=True)"""
        qs1 = Expenses.objects.filter(shop=shop, account__isnull=True).select_related('account')
        created1, skipped1, updated1 = self._bulk_create_ledger_entries(
            qs1, 'Expenses', 'expense', 'out', accounts, batch_size, dry_run
        )

        qs2 = ProductVariantExpense.objects.filter(shop=shop, account__isnull=True).select_related('account')
        created2, skipped2, updated2 = self._bulk_create_ledger_entries(
            qs2, 'ProductVariantExpense', 'expense', 'out', accounts, batch_size, dry_run
        )

        return created1 + created2, skipped1 + skipped2, updated1 + updated2

    def handle_completed_bookings_security(self, shop, accounts, batch_size, dry_run):
        """Handle completed bookings with no security payments as security out"""
        qs = BookingV2.objects.filter(
            shop=shop,
            booking_status='completed',
            security_amount__gt=0
        ).exclude(
            id__in=SecurityDepositPayment.objects.filter(booking__shop=shop).values('booking_id')
        )

        ledger_entries = []
        for booking in qs:
            if not LedgerEntry.objects.filter(
                deleted__isnull=True,
                reference_type='BookingV2',
                reference_id=booking.id,
                transaction_type='security_out'
            ).exists():
                # Derive account from the booking's existing payments; fallback to cash.
                first_payment = booking.payments.filter(
                    account__isnull=False, account__deleted__isnull=True
                ).first()
                resolved_account = (
                    first_payment.account if first_payment else None
                ) or accounts['cash']
                resolved_method = 'cash' if resolved_account == accounts['cash'] else 'upi'

                entry = LedgerEntry(
                    shop=shop,
                    account=resolved_account,
                    amount=booking.security_amount,
                    signed_amount=-booking.security_amount,
                    direction='out',
                    transaction_type='security_out',
                    payment_method=resolved_method,
                    reference_id=booking.id,
                    reference_type='BookingV2',
                    created_at=booking.completed_at or timezone.now(),
                    notes=f'Completed booking security refund for {booking.id}',
                    is_system_generated=True
                )
                ledger_entries.append(entry)

        if ledger_entries and not dry_run:
            LedgerEntry.objects.bulk_create(ledger_entries)
            # Update daily balances for the newly created entries
            bulk_update_daily_balances(ledger_entries)
            return len(ledger_entries), 0
        elif ledger_entries and dry_run:
            self.stdout.write(f'  Would create {len(ledger_entries)} security out entries for completed bookings')
            return 0, 0
        return 0, 0

    def _bulk_create_ledger_entries(self, qs, reference_type, transaction_type, direction, accounts, batch_size, dry_run):
        """Helper to bulk create ledger entries with idempotency.

        Account resolution priority:
        1. Use obj.account if already set on the payment record.
        2. Fall back to default account based on payment_method:
           - cash -> cash account
           - upi, gpay, bank -> bank account

        Also backfills account_id on the source payment table for legacy records where it was null.
        """
        created = 0
        skipped = 0
        accounts_updated = 0

        while qs.exists():
            batch = list(qs[:batch_size])
            ledger_entries = []
            account_updates = {}  # {(model_class, account_id): [obj_id, ...]}

            batch_ids = [obj.id for obj in batch]
            existing_ids = set(
                LedgerEntry.objects.filter(
                    deleted__isnull=True,
                    reference_type=reference_type,
                    reference_id__in=batch_ids,
                    transaction_type=transaction_type,
                ).values_list("reference_id", flat=True)
            )

            for obj in batch:
                if obj.id in existing_ids:
                    skipped += 1
                    continue

                # Determine shop
                if reference_type == 'SalesPaymentDetails':
                    shop = obj.sale.shop
                elif reference_type in ['PaymentDetailsV2', 'SecurityDepositPayment', 'SecurityDepositAdjustment', 'RefundDetails']:
                    shop = obj.booking.shop
                elif reference_type == 'CustomerPayment':
                    shop = obj.order.shop
                else:  # Expenses, ProductVariantExpense
                    shop = obj.shop

                # Determine amount
                if reference_type in ['SalesPaymentDetails', 'PaymentDetailsV2', 'CustomerPayment']:
                    amount = obj.payment_amount
                elif reference_type in ['SecurityDepositPayment', 'SecurityDepositAdjustment']:
                    amount = obj.amount
                elif reference_type == 'RefundDetails':
                    amount = obj.refunded_amount
                else:  # Expenses, ProductVariantExpense
                    amount = obj.expense

                # Determine payment_method.
                # Use `or` chaining so None/empty values on existing fields also fall
                # through — getattr default only fires when the attribute is absent,
                # not when it is None/falsy (e.g. old records with payment_method=NULL).
                payment_method = (
                    getattr(obj, 'payment_method', None) or
                    getattr(obj, 'refund_method', None) or
                    'cash'
                )

                # Account resolution: fall back to default account by payment_method.
                # All records here have account=None (filtered by account__isnull=True).
                account = self.get_account_for_payment_method(payment_method, accounts)

                # Track records that need their account_id backfilled
                if obj.account_id is None:
                    key = (type(obj), account.id)
                    account_updates.setdefault(key, []).append(obj.id)

                created_at = getattr(obj, 'created_at', timezone.now())

                entry = LedgerEntry(
                    shop=shop,
                    account=account,
                    amount=amount,
                    signed_amount=amount if direction == 'in' else -amount,
                    direction=direction,
                    transaction_type=transaction_type,
                    payment_method=payment_method,
                    reference_id=obj.id,
                    reference_type=reference_type,
                    created_at=created_at,
                    notes=f'{transaction_type} for {reference_type} {obj.id}',
                    is_system_generated=True
                )
                ledger_entries.append(entry)

            if not dry_run:
                if ledger_entries or account_updates:
                    with transaction.atomic():
                        if ledger_entries:
                            LedgerEntry.objects.bulk_create(ledger_entries)
                            created += len(ledger_entries)
                            # Update daily balances for the newly created entries
                            bulk_update_daily_balances(ledger_entries)
                        for (model_class, account_id), obj_ids in account_updates.items():
                            model_class.objects.filter(id__in=obj_ids).update(account_id=account_id)
                            accounts_updated += len(obj_ids)
            elif ledger_entries:
                self.stdout.write(f'  Would create {len(ledger_entries)} {transaction_type} entries')
                if account_updates:
                    total = sum(len(v) for v in account_updates.values())
                    self.stdout.write(f'  Would update account_id on {total} {reference_type} records')

            # Remove processed batch
            ids = [obj.id for obj in batch]
            qs = qs.exclude(id__in=ids)

        return created, skipped, accounts_updated
