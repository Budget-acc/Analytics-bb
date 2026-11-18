from django.core.management.base import BaseCommand
from django.utils.timezone import now
from datetime import timedelta
from shop.models import ShopActivityLog
import os

class Command(BaseCommand):
    help = "Clean up old ShopActivityLog records (default: delete entries older than 30 days)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Delete logs older than N days (default: 30)"
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
            help="Number of log records to delete per batch (default: 1000)"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview the number of records that would be deleted, without deleting"
        )

    def handle(self, *args, **options):
        def _parse_env_int(key, default):
            val = os.getenv(key)
            if val is None:
                return default
            try:
                return int(val)
            except Exception:
                return default

        days = options["days"] if options["days"] is not None else _parse_env_int("LOG_CLEANUP_DAYS", 30)
        batch_size = options["batch_size"] if options["batch_size"] is not None else _parse_env_int("LOG_CLEANUP_BATCH_SIZE", 1000)
        dry_run = options["dry_run"]

        if days <= 0:
            self.stdout.write(self.style.ERROR("Invalid --days / LOG_CLEANUP_DAYS: must be > 0"))
            return
        if batch_size <= 0:
            self.stdout.write(self.style.ERROR("Invalid --batch-size / LOG_CLEANUP_BATCH_SIZE: must be > 0"))
            return

        cutoff_date = now() - timedelta(days=days)

        qs = ShopActivityLog.objects.filter(created_at__lt=cutoff_date)
        total_to_delete = qs.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"🧾 Dry run: {total_to_delete} ShopActivityLog records "
                    f"older than {days} days (before {cutoff_date.date()}) would be deleted."
                )
            )
            return

        self.stdout.write(f"🧹 Cleaning logs older than {days} days (before {cutoff_date.date()})...")
        total_deleted = 0

        while True:
            old_ids = list(qs.values_list("id", flat=True)[:batch_size])
            if not old_ids:
                break

            deleted_count = ShopActivityLog.objects.filter(id__in=old_ids).delete()[0]
            total_deleted += deleted_count
            self.stdout.write(f"Deleted {deleted_count} old log entries...")

        if total_deleted > 0:
            self.stdout.write(self.style.SUCCESS(f"Successfully deleted {total_deleted} old ShopActivityLog records"))
        else:
            self.stdout.write(self.style.WARNING("No old ShopActivityLog records found for deletion"))