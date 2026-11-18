from django.db import models
from django.utils import timezone


class PDFGenerationTask(models.Model):
    """Track PDF generation tasks with temporary storage."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('chunking', 'Chunking'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('expired', 'Expired'),
    ]

    task_id = models.CharField(max_length=255, unique=True, db_index=True)
    shop = models.ForeignKey('shop.Shop', on_delete=models.CASCADE, related_name='pdf_tasks')

    # PDF details
    s3_key = models.CharField(max_length=500, null=True, blank=True)
    download_url = models.URLField(max_length=1000, null=True, blank=True)

    # Snapshot versioning
    snapshot_version = models.CharField(max_length=16, null=True, blank=True, db_index=True)
    quantity_version = models.CharField(max_length=16, null=True, blank=True)
    layout_version = models.CharField(max_length=8, default='v1')

    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(null=True, blank=True)

    estimated_count = models.IntegerField(null=True, blank=True, help_text="Estimated product count")
    estimated_time = models.CharField(max_length=50, null=True, blank=True, help_text="Estimated generation time")
    actual_generation_time = models.FloatField(null=True, blank=True, help_text="Actual time in seconds")
    total_chunks = models.IntegerField(null=True, blank=True, help_text="For parallel processing")
    request_hash = models.CharField(max_length=32, null=True, blank=True, db_index=True)

    # Cache optimization flags
    was_cached = models.BooleanField(default=False)
    cache_hit_type = models.CharField(
        max_length=20,
        choices=[('none', 'None'), ('full', 'Full'), ('partial', 'Partial')],
        default='none'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    url_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Request parameters (for regeneration if needed)
    request_params = models.JSONField(default=dict)

    class Meta:
        db_table = 'pdf_generation_tasks'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'url_expires_at']),
            models.Index(fields=['shop', 'created_at']),
            models.Index(fields=['snapshot_version', 'quantity_version']),
            models.Index(fields=['shop', 'request_hash']),
            models.Index(fields=['request_hash']),
        ]

    def is_url_expired(self):
        """Check if PDF has expired."""
        return self.url_expires_at and timezone.now() > self.url_expires_at

    def mark_expired(self):
        """Mark task as expired."""
        self.status = 'expired'
        self.save(update_fields=['status'])
