import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("api_requests")


@shared_task(name="pdf_service.tasks.cleanup_old_pdfs_from_s3")
def cleanup_old_pdfs_from_s3():
    """
    Daily cleanup task that deletes old PDF files from S3 for both
    PDFTask (ledger/transaction) and PDFGenerationTask (product reports).
    Retention period is controlled by PDF_RETENTION_HOURS setting (default 24h).
    """
    from pdf_service.models import PDFTask
    from services.models import PDFGenerationTask
    from services.s3_service import S3Service

    retention_hours = getattr(settings, "PDF_RETENTION_HOURS", 24)
    cutoff = timezone.now() - timedelta(hours=retention_hours)

    total_deleted = 0

    # --- Cleanup PDFTask (ledger / transaction PDFs) ---
    pdf_tasks = PDFTask.objects.filter(
        s3_key__isnull=False,
        created_at__lt=cutoff,
    ).exclude(s3_key="")

    ledger_count = 0
    for task in pdf_tasks:
        S3Service.delete_file(task.s3_key)
        task.s3_key = None
        task.download_url = None
        task.status = "expired"
        task.save(update_fields=["s3_key", "download_url", "status"])
        ledger_count += 1

    logger.info(f"[PDF Cleanup] Deleted {ledger_count} ledger/transaction PDFs from S3")
    total_deleted += ledger_count

    # --- Cleanup PDFGenerationTask (product report PDFs) ---
    # Skip base-case cached PDFs (was_cached=True, cache_hit_type='full') — they are intentionally reused
    product_tasks = PDFGenerationTask.objects.filter(
        s3_key__isnull=False,
        created_at__lt=cutoff,
    ).exclude(s3_key="").exclude(was_cached=True, cache_hit_type="full")

    product_count = 0
    for task in product_tasks:
        S3Service.delete_file(task.s3_key)
        task.s3_key = None
        task.download_url = None
        task.status = "expired"
        task.save(update_fields=["s3_key", "download_url", "status"])
        product_count += 1

    logger.info(f"[PDF Cleanup] Deleted {product_count} product report PDFs from S3")
    total_deleted += product_count

    logger.error(f"[PDF Cleanup] Total PDFs deleted from S3: {total_deleted}")
    return {"deleted": total_deleted, "ledger_transaction": ledger_count, "product_report": product_count}
