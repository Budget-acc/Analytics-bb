
from django.core.management.base import BaseCommand
from django.utils.timezone import now
from datetime import timedelta
from booking_v2.models import ProductVariantAvailability

class Command(BaseCommand):
    help = 'Clean up old ProductVariantAvailability records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Delete records older than N days (default: 7)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to delete per batch (default: 1000)'
        )

    def handle(self, *args, **options):
        cutoff_date = now().date() - timedelta(days=options['days'])
        batch_size = options['batch_size']
        
        total_deleted = 0
        
        while True:
            # Get IDs in batches
            old_ids = list(
                ProductVariantAvailability.objects
                .filter(date__lt=cutoff_date)
                .values_list('id', flat=True)[:batch_size]
            )
            
            if not old_ids:
                break
                
            # Delete the batch
            deleted_count = ProductVariantAvailability.objects.filter(
                id__in=old_ids
            ).delete()[0]
            
            total_deleted += deleted_count
            self.stdout.write(f"Deleted {deleted_count} records...")
            
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully deleted {total_deleted} old availability records"
            )
        )

# add crontab
# 0 2 * * * /path/to/manage.py cleanup_availability --days=7