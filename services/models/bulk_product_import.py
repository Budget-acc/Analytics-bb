from django.db import models


class BulkProductImport(models.Model):
    """
    Tracks a single admin-initiated bulk product creation job sourced from a
    Google Drive folder. Processing happens entirely in background Celery
    tasks (see services/tasks/bulk_import_tasks.py) - this row is the
    durable, pollable record of that job's progress.
    """

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("validating", "Validating"),
        ("listing", "Listing"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("completed_with_errors", "Completed with errors"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )

    shop = models.ForeignKey("shop.Shop", on_delete=models.CASCADE, related_name="bulk_product_imports")
    shop_service = models.ForeignKey(
        "services.ShopOwnerService", on_delete=models.CASCADE, related_name="bulk_product_imports"
    )
    created_by = models.ForeignKey(
        "authentication.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bulk_product_imports",
    )
    drive_folder_id = models.CharField(max_length=255, db_index=True)
    default_quantity = models.PositiveIntegerField(default=1)

    # Configurable image-processing settings for this job (defaulted from
    # Django settings at creation time if not explicitly supplied by the caller).
    target_resolution = models.PositiveIntegerField(default=1200)
    max_image_bytes = models.PositiveIntegerField(default=2_500_000)
    jpeg_quality_start = models.PositiveSmallIntegerField(default=90)
    jpeg_quality_min = models.PositiveSmallIntegerField(default=35)

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default="pending", db_index=True)
    cancel_requested = models.BooleanField(default=False)

    total_items = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)

    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["shop", "drive_folder_id", "status"]),
        ]

    def __str__(self):
        return f"BulkProductImport({self.id}) shop={self.shop_id} status={self.status}"
