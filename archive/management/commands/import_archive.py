from django.core.management.base import BaseCommand

from archive.importer import import_all, import_prefix, pending_manifests
from archive.models import ImportBatch
from archive.s3 import ArchiveBucket


class Command(BaseCommand):
    help = "ANALYTICS app: load new archive batches from S3. Use --list to only see what is waiting."

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", dest="list_only", help="Only list batches waiting to be loaded.")
        parser.add_argument("--limit", type=int, default=None, help="Load at most this many batches.")

    def handle(self, *args, list_only, limit, **options):
        bucket = ArchiveBucket()
        keys = pending_manifests(bucket)
        self.stdout.write(f"Folder: {import_prefix()}  |  waiting: {len(keys)}")
        for key in keys[:limit]:
            self.stdout.write(f"  {key}")
        if list_only:
            return

        result = import_all(bucket, limit=limit)
        self.stdout.write(f"Loaded: {result['loaded']}  |  failed: {result['failed']}")
        for batch in ImportBatch.objects.order_by("-id")[: result["loaded"] + result["failed"]]:
            self.stdout.write(f"  run={batch.run_id} batch={batch.batch_id} shop={batch.shop_id} "
                              f"{batch.kind} [{batch.status}] rows={batch.total_rows} {batch.error}")
            for t in batch.tables.all():
                self.stdout.write(f"      {t.table_name}: file={t.rows_in_file} new={t.rows_inserted} "
                                  f"updated={t.rows_updated} dates={t.date_from}..{t.date_to} total={t.total_amount}")