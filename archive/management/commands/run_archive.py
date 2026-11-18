import json

from django.core.management.base import BaseCommand

from archive.service import run_archive
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the archive job now. Use --dry-run first: it only counts, nothing is sent or deleted."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--shop-id", type=int, action="append", dest="shop_ids",
                            help="Only this shop (can be given more than once).")


    def handle(self, *args, dry_run, shop_ids, **options):
        try:
            run = run_archive(dry_run=dry_run, shop_ids=shop_ids)
        except ValueError as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"ArchiveRun {run.id}: {run.status}")
        for batch in run.batches.all():
            self.stdout.write(f"  batch {batch.id} shop={batch.shop_id} {batch.kind} [{batch.status}] {batch.error}")
            self.stdout.write("    " + json.dumps(batch.manifest_counts))