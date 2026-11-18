import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from tailored.models import TailorOrderSecurityPayment

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


class Command(BaseCommand):
    help = (
        "Fix TailorOrderSecurityPayment paid_at values.\n"
        "Updates records where is_paid=True and paid_at IS NULL "
        "by setting paid_at = created_at."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview affected records without updating",
        )

        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        queryset = (
            TailorOrderSecurityPayment.objects
            .filter(
                is_paid=True,
                paid_at__isnull=True,
            )
            .only("id", "created_at", "paid_at")
            .order_by("id")
        )

        total_records = queryset.count()

        if total_records == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "No records found to update."
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"Found {total_records} records to fix."
            )
        )

        if dry_run:
            for record in queryset.iterator(chunk_size=BATCH_SIZE):
                self.stdout.write(
                    f"[DRY RUN] id={record.id} "
                    f"paid_at=NULL -> {record.created_at}"
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run complete. {total_records} records would be updated."
                )
            )
            return

        if not force:
            confirm = input(
                "Proceed with update? (yes/no): "
            ).strip().lower()

            if confirm not in {"yes", "y"}:
                self.stdout.write(
                    self.style.ERROR("Aborted.")
                )
                return

        updated_count = 0
        batch = []

        try:
            for record in queryset.iterator(chunk_size=BATCH_SIZE):
                record.paid_at = record.created_at
                batch.append(record)

                if len(batch) >= BATCH_SIZE:
                    self._bulk_update(batch)
                    updated_count += len(batch)

                    self.stdout.write(
                        f"Updated {updated_count}/{total_records}"
                    )

                    batch = []

            if batch:
                self._bulk_update(batch)
                updated_count += len(batch)

            self.stdout.write(
                self.style.SUCCESS(
                    f"Completed successfully. "
                    f"Updated {updated_count} records."
                )
            )

        except Exception as exc:
            logger.exception(
                "Failed fixing tailor security payment paid_at values"
            )

            self.stdout.write(
                self.style.ERROR(
                    f"Command failed: {exc}"
                )
            )

    @staticmethod
    def _bulk_update(records):
        with transaction.atomic():
            TailorOrderSecurityPayment.objects.bulk_update(
                records,
                ["paid_at"],
                batch_size=BATCH_SIZE,
            )