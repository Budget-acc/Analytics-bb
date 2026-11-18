import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from shop.models import Shop
from finance.daily_balance_utils import recalculate_daily_balances_for_shop

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Recalculate daily balances for shops based on existing ledger entries. Clears existing daily balances first.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--shop-id',
            type=int,
            default=None,
            help='Specific shop ID to recalculate (default: all shops)'
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
        self.stdout.write(f'Recalculating daily balances for {total_shops} shop(s)...')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No database changes will be made'))
        elif not force:
            # Show confirmation prompt
            self.stdout.write(self.style.WARNING(
                'WARNING: This will clear all existing daily balances and recalculate them from ledger entries.'
            ))
            confirm = input('Do you want to proceed? (yes/no): ').strip().lower()
            if confirm not in ['yes', 'y']:
                self.stdout.write(self.style.ERROR('Operation cancelled.'))
                return

        total_accounts = 0
        total_days = 0
        total_balance = 0

        for shop in shops:
            self.stdout.write(f'\nProcessing shop: {shop.name} (ID: {shop.id})')

            if not dry_run:
                result = recalculate_daily_balances_for_shop(shop, clear_existing=True)
            else:
                # In dry run mode, show what would be processed
                result = self._dry_run_calculate(shop)

            total_accounts += result['accounts_processed']
            total_days += result['days_processed']
            total_balance += result['total_balance']

            self.stdout.write(self.style.SUCCESS(
                f'  Accounts processed: {result["accounts_processed"]}'
            ))
            self.stdout.write(f'  Days processed: {result["days_processed"]}')
            self.stdout.write(f'  Final balance: {result["total_balance"]}')

        # Summary
        self.stdout.write(self.style.SUCCESS(f'\nRecalculation complete:'))
        self.stdout.write(f'  Total accounts: {total_accounts}')
        self.stdout.write(f'  Total days: {total_days}')
        self.stdout.write(f'  Final total balance: {total_balance}')

    def _dry_run_calculate(self, shop):
        """
        Simulate the calculation without writing to database.
        """
        from finance.models import LedgerEntry
        from decimal import Decimal

        ledger_entries = LedgerEntry.objects.filter(
            shop=shop,
            deleted__isnull=True
        ).select_related('account').order_by('created_at')

        if not ledger_entries.exists():
            self.stdout.write(self.style.WARNING('  No ledger entries found for this shop'))
            return {
                'accounts_processed': 0,
                'days_processed': 0,
                'total_balance': Decimal('0')
            }

        # Group entries by (account, date)
        daily_data = {}
        for entry in ledger_entries:
            entry_date = entry.created_at.date()
            key = (entry.account_id, entry_date)

            if key not in daily_data:
                daily_data[key] = {
                    'total_in': Decimal('0'),
                    'total_out': Decimal('0'),
                }

            if entry.direction == 'in':
                daily_data[key]['total_in'] += entry.amount
            else:
                daily_data[key]['total_out'] += entry.amount

        # Calculate final balances
        account_balances = {}
        sorted_keys = sorted(daily_data.keys(), key=lambda x: (x[0], x[1]))

        for key in sorted_keys:
            account_id, date = key

            if account_id not in account_balances:
                account_balances[account_id] = Decimal('0')

            data = daily_data[key]
            account_balances[account_id] = account_balances[account_id] + data['total_in'] - data['total_out']

        total_balance = sum(balance for balance in account_balances.values())

        self.stdout.write(f'  Would process {len(daily_data)} daily records')

        return {
            'accounts_processed': len(account_balances),
            'days_processed': len(daily_data),
            'total_balance': total_balance,
        }
