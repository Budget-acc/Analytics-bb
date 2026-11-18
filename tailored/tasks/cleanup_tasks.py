# tailored/tasks/cleanup_tasks.py
from celery import shared_task
import logging
import os
import time
import tempfile
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger("api_requests")

BATCH_SIZE = 100


@shared_task(name="tailored.tasks.cleanup_old_tailor_order_temp_files")
def cleanup_old_tailor_order_temp_files():
    """
    Clean up temp files older than 24 hours.
    Runs daily via Celery beat.
    """
    temp_dir = Path(tempfile.gettempdir()) / "tailor_order_docs"
    if not temp_dir.exists():
        return

    cutoff_time = time.time() - (24 * 3600)  # 24 hours ago
    deleted_count = 0

    for file_path in temp_dir.glob("*"):
        try:
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                os.unlink(file_path)
                deleted_count += 1
        except Exception as e:
            logger.error(f"Failed to delete old temp file {file_path}: {str(e)}")

    logger.info(f"Cleaned up {deleted_count} old tailor order temp files")


@shared_task(
    name="tailored.tasks.cleanup_old_tailor_order_documents",
    bind=True,
    max_retries=3,
)
def cleanup_old_tailor_order_documents(self):
    """
    Cleanup tailor order documents older than retention period.

    Flow:
    1. Fetch tailor orders in batches
    2. Save document URLs
    3. Clear DB fields
    4. Commit DB transaction
    5. Trigger async S3 deletion task
    """
    from tailored.models import TailorOrder
    from .document_tasks import cleanup_s3_tailor_order_documents_task

    retention_days = getattr(settings, "TAILOR_ORDER_DOCUMENT_RETENTION_DAYS", 30)
    cutoff_date = timezone.now() - timedelta(days=retention_days)

    processed_orders = 0
    total_documents = 0

    logger.info(
        "[Tailor Order Cleanup] Starting | retention_days=%s | cutoff=%s",
        retention_days,
        cutoff_date,
    )

    while True:

        orders = list(
            TailorOrder.objects.filter(
                status="completed",
                completed_at__isnull=False,
                completed_at__lte=cutoff_date,
            )
            .exclude(documents=[])
            .only("id", "documents", "documents_details")[:BATCH_SIZE]
        )

        if not orders:
            break

        for order in orders:

            document_urls = order.documents or []

            if not isinstance(document_urls, list) or not document_urls:
                continue

            logger.info(
                "[Tailor Order Cleanup] Processing order %s | docs=%s",
                order.id,
                len(document_urls),
            )

            # Save URLs before clearing
            urls_to_delete = list(document_urls)

            # ---- DB UPDATE ----
            with transaction.atomic():

                order.documents = []

                order.documents_details = {
                    "status": "cleaned",
                    "cleaned_at": timezone.now().isoformat(),
                    "total_count": 0,
                    "uploaded_count": 0,
                }

                order.save(update_fields=["documents", "documents_details"])

            # ---- ASYNC S3 CLEANUP ----
            cleanup_s3_tailor_order_documents_task.apply_async(args=[urls_to_delete])

            processed_orders += 1
            total_documents += len(urls_to_delete)

        logger.info(
            "[Tailor Order Cleanup] Batch complete | orders=%s | docs=%s",
            processed_orders,
            total_documents,
        )

    logger.error(
        "[Tailor Order Cleanup] Finished | orders=%s | documents=%s",
        processed_orders,
        total_documents,
    )

    return {
        "processed_orders": processed_orders,
        "documents_scheduled_for_deletion": total_documents,
    }
