# booking_v2/tasks/cleanup_tasks.py
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
MAX_ITERATIONS = 1000


@shared_task(name="booking_v2.tasks.cleanup_old_temp_files")
def cleanup_old_temp_files():
    """
    Clean up temp files older than 24 hours
    Runs daily via Celery beat
    """
    temp_dir = Path(tempfile.gettempdir()) / "booking_docs"
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

    logger.info(f"Cleaned up {deleted_count} old temp files")


@shared_task(
    name="booking_v2.tasks.cleanup_old_booking_documents",
    bind=True,
    max_retries=3,
)
def cleanup_old_booking_documents(self):
    """
    Cleanup booking documents older than retention period.

    Flow:
    1. Fetch bookings in batches
    2. Save document URLs
    3. Clear DB fields
    4. Commit DB transaction
    5. Trigger async S3 deletion task
    """
    from booking_v2.models import BookingV2
    from .document_tasks import cleanup_s3_documents_task

    retention_days = getattr(settings, "BOOKING_DOCUMENT_RETENTION_DAYS", 30)
    cutoff_date = timezone.now() - timedelta(days=retention_days)

    processed_bookings = 0
    total_documents = 0

    logger.info(
        "[Booking Cleanup] Starting | retention_days=%s | cutoff=%s",
        retention_days,
        cutoff_date,
    )

    while True:

        bookings = list(
            BookingV2.objects.filter(
                booking_status="completed",
                completed_at__isnull=False,
                completed_at__lte=cutoff_date,
            )
            .exclude(documents=[])
            .only("id", "documents", "documents_details")[:BATCH_SIZE]
        )

        if not bookings:
            break

        for booking in bookings:

            document_urls = booking.documents or []

            if not isinstance(document_urls, list) or not document_urls:
                continue

            logger.info(
                "[Booking Cleanup] Processing booking %s | docs=%s",
                booking.id,
                len(document_urls),
            )

            # Save URLs before clearing
            urls_to_delete = list(document_urls)

            # ---- DB UPDATE ----
            with transaction.atomic():

                booking.documents = []

                booking.documents_details = {
                    "status": "cleaned",
                    "cleaned_at": timezone.now().isoformat(),
                    "total_count": 0,
                    "uploaded_count": 0,
                }

                booking.save(update_fields=["documents", "documents_details"])

            # ---- ASYNC S3 CLEANUP ----
            cleanup_s3_documents_task.apply_async(args=[urls_to_delete])

            processed_bookings += 1
            total_documents += len(urls_to_delete)

        logger.info(
            "[Booking Cleanup] Batch complete | bookings=%s | docs=%s",
            processed_bookings,
            total_documents,
        )

    logger.error(
        "[Booking Cleanup] Finished | bookings=%s | documents=%s",
        processed_bookings,
        total_documents,
    )

    return {
        "processed_bookings": processed_bookings,
        "documents_scheduled_for_deletion": total_documents,
    }
