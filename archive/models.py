from django.db import models
from django.utils import timezone


class ArchiveRun(models.Model):
    """One row per night (or per manual run)."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"
        FAILED = "failed", "Finished with errors"

    cutoff = models.DateTimeField(help_text="Data before this moment is archived (00:00 IST of yesterday).")
    shop_ids = models.JSONField(default=list)
    dry_run = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ArchiveRun {self.id} cutoff={self.cutoff:%d-%m-%Y}"


class ArchiveBatch(models.Model):
    """
    One group of ~500 main records of one shop and one kind.

        exporting -> uploaded -> verified -> purged
                         \\-> failed (at any step)
    """

    class Kind(models.TextChoices):
        BOOKING = "booking", "Bookings"
        SALE = "sale", "Sales"
        EXPENSE = "expense", "Expenses"
        PRODUCT_EXPENSE = "product_expense", "Product expenses (not linked)"
        DAILY_BALANCE = "daily_balance", "Daily balance rows"

    class Status(models.TextChoices):
        EXPORTING = "exporting", "Exporting"
        UPLOADED = "uploaded", "Uploaded to S3"
        VERIFIED = "verified", "Verified in S3"
        PURGED = "purged", "Deleted from live"
        DRY_RUN = "dry_run", "Dry run (nothing sent or deleted)"
        FAILED = "failed", "Failed"

    run = models.ForeignKey(ArchiveRun, on_delete=models.PROTECT, related_name="batches")
    shop_id = models.BigIntegerField(db_index=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    record_ids = models.JSONField(default=list, help_text="IDs of the main records in this batch.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.EXPORTING, db_index=True)
    s3_prefix = models.CharField(max_length=512, blank=True, default="")

    # {table: [ids]} of rows that are deleted from live (copy-only rows are not listed).
    moved_ids = models.JSONField(default=dict, blank=True)
    # {table: {"rows": n, "moved": n}} written at export time.
    manifest_counts = models.JSONField(default=dict, blank=True)
    # {table: rows_deleted} written by the purge step.
    purge_counts = models.JSONField(default=dict, blank=True)

    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["shop_id", "kind", "status"])]

    def __str__(self):
        return f"ArchiveBatch {self.id} shop={self.shop_id} {self.kind} [{self.status}]"







# --------------------------------------------------------------------------- #
# Used by the ANALYTICS app only: one row per S3 folder it loaded.
# --------------------------------------------------------------------------- #
class ImportBatch(models.Model):
    class Status(models.TextChoices):
        LOADING = "loading", "Loading"
        LOADED = "loaded", "Loaded"
        FAILED = "failed", "Failed"

    s3_prefix = models.CharField(max_length=512, unique=True, help_text="S3 folder this batch came from.")
    run_id = models.BigIntegerField(help_text="ArchiveRun id in the live app.")
    batch_id = models.BigIntegerField(help_text="ArchiveBatch id in the live app.")
    shop_id = models.BigIntegerField(db_index=True)
    kind = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.LOADING, db_index=True)
    data_from = models.DateField(null=True, blank=True)
    data_to = models.DateField(null=True, blank=True)
    total_rows = models.IntegerField(default=0)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["shop_id", "status"])]

    def __str__(self):
        return f"ImportBatch run={self.run_id} batch={self.batch_id} shop={self.shop_id} {self.kind} [{self.status}]"


class ImportTableLog(models.Model):
    """One row per table inside an ImportBatch."""

    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="tables")
    table_name = models.CharField(max_length=128)
    rows_in_file = models.IntegerField(default=0)
    rows_inserted = models.IntegerField(default=0)
    rows_updated = models.IntegerField(default=0)
    rows_failed = models.IntegerField(default=0)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    checksum_ok = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.table_name}: {self.rows_inserted} new, {self.rows_updated} updated"
