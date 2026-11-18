from django.db import models


class BulkProductImportItem(models.Model):
    """
    One row per Google Drive image file discovered for a BulkProductImport job.
    Durable per-file audit trail: every file's lifecycle is tracked here, so
    progress, failures, and duplicates are all queryable without holding
    anything in memory or in a single unbounded JSON blob.
    """

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("created", "Product created"),
        ("duplicate", "Duplicate - skipped"),
        ("invalid", "Invalid file - skipped"),
        ("failed", "Failed"),
    )

    bulk_import = models.ForeignKey(
        "services.BulkProductImport", on_delete=models.CASCADE, related_name="items"
    )
    drive_file_id = models.CharField(max_length=255)
    filename = models.CharField(max_length=255)
    # Normalized product name used for duplicate detection: lowercased, extension
    # stripped, whitespace/-/_ collapsed to single spaces.
    normalized_name = models.CharField(max_length=200, db_index=True)

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="pending")
    image_url = models.URLField(max_length=500, blank=True, null=True)
    thumbnail_url = models.URLField(max_length=500, blank=True, null=True)
    product = models.ForeignKey(
        "services.Product", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bulk_import_items",
    )
    error_message = models.TextField(blank=True, default="")
    attempt_count = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["bulk_import", "status"]),
        ]

    def __str__(self):
        return f"BulkProductImportItem({self.id}) {self.filename} status={self.status}"
