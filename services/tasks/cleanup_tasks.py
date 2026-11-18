from celery import shared_task
import logging
import time
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("api_requests")

LOCK_TIMEOUT = getattr(settings, 'PDF_LOCK_TIMEOUT', 600)


def acquire_pdf_lock(lock_key, timeout=LOCK_TIMEOUT):
    """Acquire distributed lock for PDF generation."""
    acquired = cache.add(lock_key, {
        'acquired_at': time.time(),
        'hostname': getattr(settings, 'HOSTNAME', 'unknown')
    }, timeout=timeout)

    if acquired:
        logger.info(f"[Lock] Acquired: {lock_key}")
    else:
        logger.info(f"[Lock] Failed to acquire: {lock_key}")

    return acquired


def release_pdf_lock(lock_key):
    """Release distributed lock."""
    cache.delete(lock_key)
    logger.info(f"[Lock] Released: {lock_key}")


@shared_task
def cleanup_old_pdfs_async(s3_keys):
    """Async cleanup of old PDFs from S3."""
    from services.s3_service import S3Service

    deleted_count = 0
    for s3_key in s3_keys:
        try:
            S3Service.delete_file(s3_key)
            deleted_count += 1
        except Exception as e:
            logger.warning(f"[PDF Cleanup] Delete failed for {s3_key}: {e}")

    logger.info(f"[PDF Cleanup] Cleaned up {deleted_count}/{len(s3_keys)} old PDFs")
