from django.db import models
from django.utils import timezone
from datetime import timedelta


class PDFTask(models.Model):
    """Generic PDF generation task tracker."""

    PDF_TYPE_CHOICES = [
        ('product_report', 'Product Report'),
        ('ledger_book', 'Ledger Book'),
        ('transaction_report', 'Transaction Report'),
        ('account_history', 'Account History'),
        ('tax_report', 'Tax Report'),
        ('client_details', 'Client Details'),
        ('booking_list', 'Booking List'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('expired', 'Expired'),
    ]

    # Task identification
    task_id = models.CharField(max_length=255, unique=True, db_index=True)
    pdf_type = models.CharField(max_length=50, choices=PDF_TYPE_CHOICES, db_index=True)

    # Relationships
    shop = models.ForeignKey('shop.Shop', on_delete=models.CASCADE, related_name='pdf_service_tasks')
    user = models.ForeignKey('authentication.User', on_delete=models.CASCADE, related_name='pdf_service_tasks')

    # PDF storage
    s3_key = models.CharField(max_length=500, null=True, blank=True)
    download_url = models.URLField(max_length=1000, null=True, blank=True)
    url_expires_at = models.DateTimeField(null=True, blank=True)

    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    error_message = models.TextField(null=True, blank=True)

    # Request details
    request_params = models.JSONField(default=dict)
    request_hash = models.CharField(max_length=64, db_index=True, null=True, blank=True)

    # Metadata
    estimated_count = models.IntegerField(null=True, blank=True)
    estimated_time = models.CharField(max_length=50, null=True, blank=True)
    actual_generation_time = models.FloatField(null=True, blank=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'pdf_service_task'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['shop', 'pdf_type', 'status', '-created_at']),
            models.Index(fields=['request_hash', '-created_at']),
        ]

    def __str__(self):
        return f"{self.pdf_type} - {self.task_id} ({self.status})"

    def mark_processing(self):
        self.status = 'processing'
        self.started_at = timezone.now()
        self.save(update_fields=['status', 'started_at'])

    def mark_completed(self, s3_key, download_url, generation_time, file_size=None):
        self.status = 'completed'
        self.s3_key = s3_key
        self.download_url = download_url
        self.url_expires_at = timezone.now() + timedelta(hours=24)
        self.actual_generation_time = generation_time
        self.file_size_bytes = file_size
        self.completed_at = timezone.now()
        self.save(update_fields=[
            'status', 's3_key', 'download_url', 'url_expires_at',
            'actual_generation_time', 'file_size_bytes', 'completed_at'
        ])

    def mark_failed(self, error_message):
        self.status = 'failed'
        self.error_message = error_message[:1000]
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'error_message', 'completed_at'])
