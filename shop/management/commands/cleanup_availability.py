
from django.core.management.base import BaseCommand
from django.utils.timezone import now
from datetime import timedelta
from booking_v2.models import ProductVariantAvailability
import os

class Command(BaseCommand):
    help = 'Clean up old ProductVariantAvailability records (default: delete entries older than 7 days)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Delete records older than N days (default: 7)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=None,
            help='Number of records to delete per batch (default: 1000)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview the number of records that would be deleted, without deleting'
        )

    def handle(self, *args, **options):
        def _parse_env_int(key, default):
            """Helper to parse integer from environment variable"""
            val = os.getenv(key)
            if val is None:
                return default
            try:
                return int(val)
            except Exception:
                return default

        # Get values from options or environment variables
        days = options['days'] if options['days'] is not None else _parse_env_int('AVAILABILITY_CLEANUP_DAYS', 7)
        batch_size = options['batch_size'] if options['batch_size'] is not None else _parse_env_int('AVAILABILITY_CLEANUP_BATCH_SIZE', 1000)
        dry_run = options['dry_run']

        # Validation
        if days <= 0:
            self.stdout.write(self.style.ERROR('Invalid --days / AVAILABILITY_CLEANUP_DAYS: must be > 0'))
            return
        if batch_size <= 0:
            self.stdout.write(self.style.ERROR('Invalid --batch-size / AVAILABILITY_CLEANUP_BATCH_SIZE: must be > 0'))
            return

        cutoff_date = now().date() - timedelta(days=days)

        # Get queryset of records to delete
        qs = ProductVariantAvailability.objects.filter(date__lt=cutoff_date)
        total_to_delete = qs.count()

        # Dry run mode - just preview
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'🧾 Dry run: {total_to_delete} ProductVariantAvailability records '
                    f'older than {days} days (before {cutoff_date}) would be deleted.'
                )
            )
            return

        # Actual deletion
        self.stdout.write(f'🧹 Cleaning availability records older than {days} days (before {cutoff_date})...')
        total_deleted = 0

        while True:
            # Get IDs in batches
            old_ids = list(qs.values_list('id', flat=True)[:batch_size])

            if not old_ids:
                break

            # Delete the batch
            deleted_count = ProductVariantAvailability.objects.filter(
                id__in=old_ids
            ).delete()[0]

            total_deleted += deleted_count
            self.stdout.write(f'Deleted {deleted_count} old availability records...')

        if total_deleted > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ Successfully deleted {total_deleted} old ProductVariantAvailability records'
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING('No old ProductVariantAvailability records found for deletion')
            )

# add crontab
# 0 2 * * * /path/to/manage.py cleanup_availability --days=7