"""
Utility functions for managing daily balances.
Handles updating daily balance records when ledger entries are created or modified.
"""

import logging
from decimal import Decimal
from django.db.models import F
from django.db import IntegrityError
from django.db import transaction
from .models import DailyBalance, LedgerEntry

logger = logging.getLogger(__name__)


def update_daily_balance_for_entry(ledger_entry):
    """
    Update the daily balance for a shop and account based on a single ledger entry.
    
    Args:
        ledger_entry: LedgerEntry instance
    """
    entry_date = ledger_entry.created_at.date()
    shop = ledger_entry.shop
    account = ledger_entry.account
    amount = ledger_entry.amount
    direction = ledger_entry.direction
    
    with transaction.atomic():
        previous_daily_balance = DailyBalance.objects.select_for_update().filter(
            shop=shop,
            account=account,
            date__lt=entry_date,
            deleted__isnull=True,
        ).order_by('-date').first()
        opening_balance = previous_daily_balance.closing_balance if previous_daily_balance else Decimal('0')

        try:
            daily_balance, created = DailyBalance.objects.select_for_update().get_or_create(
                shop=shop,
                account=account,
                date=entry_date,
                defaults={
                    'opening_balance': opening_balance,
                    'closing_balance': opening_balance,
                    'total_in': Decimal('0'),
                    'total_out': Decimal('0'),
                }
            )
        except IntegrityError:
            daily_balance = DailyBalance.objects.select_for_update().get(
                shop=shop,
                account=account,
                date=entry_date,
                deleted__isnull=True,
            )
        
        # Update totals based on direction
        if direction == 'in':
            daily_balance.total_in += amount
        else:  # direction == 'out'
            daily_balance.total_out += amount
        
        # Recalculate closing balance
        daily_balance.closing_balance = daily_balance.opening_balance + daily_balance.total_in - daily_balance.total_out
        
        daily_balance.save(update_fields=['total_in', 'total_out', 'closing_balance'])


def bulk_update_daily_balances(ledger_entries):
    """
    Bulk update daily balances for multiple ledger entries.
    
    Args:
        ledger_entries: List of LedgerEntry instances
    """
    if not ledger_entries:
        return
    
    # Group entries by (shop, account, date)
    balance_updates = {}
    
    for entry in ledger_entries:
        entry_date = entry.created_at.date()
        key = (entry.shop_id, entry.account_id, entry_date)
        
        if key not in balance_updates:
            balance_updates[key] = {
                'shop_id': entry.shop_id,
                'account_id': entry.account_id,
                'date': entry_date,
                'total_in': Decimal('0'),
                'total_out': Decimal('0'),
            }
        
        if entry.direction == 'in':
            balance_updates[key]['total_in'] += entry.amount
        else:
            balance_updates[key]['total_out'] += entry.amount
    
    # Process in account/date order so opening balance can be carried correctly.
    sorted_keys = sorted(balance_updates.keys(), key=lambda x: (x[1], x[2]))

    # Update or create daily balance records
    with transaction.atomic():
        for key in sorted_keys:
            update_data = balance_updates[key]
            shop_id, account_id, date = key

            previous_daily_balance = DailyBalance.objects.select_for_update().filter(
                shop_id=shop_id,
                account_id=account_id,
                date__lt=date,
                deleted__isnull=True,
            ).order_by('-date').first()
            opening_balance = previous_daily_balance.closing_balance if previous_daily_balance else Decimal('0')

            try:
                daily_balance, created = DailyBalance.objects.select_for_update().get_or_create(
                    shop_id=shop_id,
                    account_id=account_id,
                    date=date,
                    defaults={
                        'opening_balance': opening_balance,
                        'closing_balance': opening_balance + update_data['total_in'] - update_data['total_out'],
                        'total_in': update_data['total_in'],
                        'total_out': update_data['total_out'],
                    }
                )
            except IntegrityError:
                # Another concurrent transaction inserted the same row. Lock and update it below.
                daily_balance = DailyBalance.objects.select_for_update().get(
                    shop_id=shop_id,
                    account_id=account_id,
                    date=date,
                    deleted__isnull=True,
                )
                created = False

            if not created:
                DailyBalance.objects.filter(pk=daily_balance.pk).update(
                    total_in=F('total_in') + update_data['total_in'],
                    total_out=F('total_out') + update_data['total_out'],
                    closing_balance=F('opening_balance') + F('total_in') - F('total_out') + update_data['total_in'] - update_data['total_out'],
                )


def recalculate_daily_balances_for_shop(shop, clear_existing=True):
    """
    Recalculate all daily balances for a shop based on existing ledger entries.
    
    This function:
    1. Optionally clears existing daily balance records
    2. Retrieves all ledger entries for the shop, ordered by created_at
    3. Recalculates opening and closing balances for each day
    4. Ensures balances carry over day to day
    
    Args:
        shop: Shop instance
        clear_existing: If True, delete all existing daily balances before recalculating
        
    Returns:
        dict: Summary with accounts_processed, days_processed, and final balances
    """
    if clear_existing:
        DailyBalance.objects.filter(shop=shop, deleted__isnull=True).delete()
    
    # Get all ledger entries for this shop, ordered by created_at
    ledger_entries = LedgerEntry.objects.filter(
        shop=shop,
        deleted__isnull=True
    ).select_related('account').order_by('created_at')
    
    if not ledger_entries.exists():
        logger.info(f"No ledger entries found for shop {shop.id}")
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
                'shop_id': shop.id,
                'account_id': entry.account_id,
                'account': entry.account,
                'date': entry_date,
                'total_in': Decimal('0'),
                'total_out': Decimal('0'),
            }
        
        if entry.direction == 'in':
            daily_data[key]['total_in'] += entry.amount
        else:
            daily_data[key]['total_out'] += entry.amount
    
    # Create daily balance records with proper opening/closing balances
    daily_balances_to_create = []
    account_balances = {}  # {account_id: running_balance}
    
    # Sort by account and date to ensure proper sequential calculation
    sorted_keys = sorted(daily_data.keys(), key=lambda x: (x[0], x[1]))
    
    with transaction.atomic():
        for key in sorted_keys:
            account_id, date = key
            data = daily_data[key]
            
            # Get opening balance from previous day
            if account_id not in account_balances:
                # First entry for this account - opening balance is 0
                account_balances[account_id] = Decimal('0')
            
            opening_balance = account_balances[account_id]
            closing_balance = opening_balance + data['total_in'] - data['total_out']
            account_balances[account_id] = closing_balance
            
            daily_balance = DailyBalance(
                shop_id=shop.id,
                account_id=account_id,
                date=date,
                opening_balance=opening_balance,
                closing_balance=closing_balance,
                total_in=data['total_in'],
                total_out=data['total_out'],
            )
            daily_balances_to_create.append(daily_balance)
        
        # Bulk create all daily balance records
        DailyBalance.objects.bulk_create(daily_balances_to_create)
    
    # Calculate total balance across all accounts
    total_balance = sum(balance for balance in account_balances.values())
    
    return {
        'accounts_processed': len(account_balances),
        'days_processed': len(daily_data),
        'total_balance': total_balance,
    }
