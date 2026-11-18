"""
Management command: cleanup_whatsapp_invoice_pdfs
==================================================

Manually runs (or previews / verifies) the same cleanup the daily Celery Beat
task `whatsapp_integration.v3.tasks.cleanup_whatsapp_invoice_pdfs` runs at 3 AM:
PERMANENTLY deletes every PDF under the `whatsapp-invoices/` S3 prefix -
every object version and delete marker, so the data is really removed on a
versioned bucket, not just hidden behind a delete marker. Nothing outside that
prefix is touched.

The root `whatsapp-invoices/` folder is preserved: its marker object is never
deleted, and is created once (zero-byte) if missing. Only PDFs and the
date-based subfolders inside it are removed.

Usage
-----
    python manage.py cleanup_whatsapp_invoice_pdfs --dry-run      # preview only
    python manage.py cleanup_whatsapp_invoice_pdfs                # delete (asks yes/no)
    python manage.py cleanup_whatsapp_invoice_pdfs --force        # delete, no prompt
    python manage.py cleanup_whatsapp_invoice_pdfs --verify-only  # count what is still stored
"""
import json

from django.core.management.base import BaseCommand

from whatsapp_integration.v3.tasks import (
    WHATSAPP_INVOICE_S3_PREFIX,
    _root_folder_marker_exists,
    run_whatsapp_invoice_pdf_cleanup,
    verify_whatsapp_invoice_pdfs_removed,
)


class Command(BaseCommand):
    help = (
        f"Permanently delete all WhatsApp invoice PDFs (all versions and delete markers) "
        f"under s3://<bucket>/{WHATSAPP_INVOICE_S3_PREFIX}."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List and count only, no deletes.")
        parser.add_argument("--force", action="store_true", help="Skip the confirmation prompt.")
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Only count PDF versions / delete markers still stored under the prefix.",
        )

    def handle(self, *args, **options):
        if options["verify_only"]:
            from django.conf import settings
            from services.s3_service import S3Service

            root = _root_folder_marker_exists(S3Service.get_client(), settings.AWS_STORAGE_BUCKET_NAME)
            root_msg = {True: "present", False: "MISSING", None: "could not be checked"}[root]
            style = self.style.SUCCESS if root else self.style.WARNING
            self.stdout.write(style(f"Root folder marker {WHATSAPP_INVOICE_S3_PREFIX}: {root_msg}"))

            remaining, samples = verify_whatsapp_invoice_pdfs_removed()
            if remaining:
                self.stdout.write(self.style.WARNING(
                    f"{remaining} PDF version(s)/delete marker(s) still stored under {WHATSAPP_INVOICE_S3_PREFIX}"
                ))
                for sample in samples:
                    self.stdout.write(f"  {sample}")
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"Nothing stored under {WHATSAPP_INVOICE_S3_PREFIX} - permanent deletion verified."
                ))
            return

        dry_run = options["dry_run"]
        if not dry_run and not options["force"]:
            confirm = input(
                f"Permanently delete ALL PDFs under {WHATSAPP_INVOICE_S3_PREFIX} (cannot be undone)? (yes/no): "
            ).strip().lower()
            if confirm not in ("yes", "y"):
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        stats = run_whatsapp_invoice_pdf_cleanup(dry_run=dry_run)
        self.stdout.write(json.dumps(stats, indent=2, default=str))

        if stats["aborted"] or stats["failed"] or stats["soft_deleted"] or stats["root_folder"] == "error":
            self.stdout.write(self.style.ERROR("Cleanup finished with errors - see the log above."))
        elif dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"Dry run complete - nothing deleted (root folder: {stats['root_folder']})."
            ))
        elif stats["remaining_after_verify"] == 0 and stats["root_folder_present"]:
            self.stdout.write(self.style.SUCCESS(
                f"Permanent deletion verified; root folder {WHATSAPP_INVOICE_S3_PREFIX} {stats['root_folder']}."
            ))
        else:
            self.stdout.write(self.style.WARNING("Cleanup stopped early or could not be verified - run again."))
