import logging

from celery import shared_task
from django.core.cache import cache

from .service import run_archive

logger = logging.getLogger("api_requests")

LOCK_KEY = "archive:nightly:lock"
LOCK_SECONDS = 60 * 60 * 3  # longer than the task time limit


@shared_task(
    name="archive.tasks.run_nightly_archive",
    bind=True,
    time_limit=60 * 60 * 2,
    soft_time_limit=60 * 60 * 2 - 120,
)
def run_nightly_archive(self, dry_run=False):
    """Celery beat calls this every night at 00:01 IST."""
    # Only one archive job at a time, across all workers.
    if not cache.add(LOCK_KEY, self.request.id or "manual", LOCK_SECONDS):
        logger.info("[Archive] another run is in progress, skipping")
        return {"status": "locked"}
    try:
        run = run_archive(dry_run=dry_run)
        return {"run_id": run.id, "status": run.status}
    finally:
<<<<<<< HEAD
        cache.delete(LOCK_KEY)
=======
        cache.delete(LOCK_KEY)




IMPORT_LOCK_KEY = "archive:import:lock"


@shared_task(
    name="archive.tasks.import_archive_batches",
    bind=True,
    time_limit=60 * 60,
    soft_time_limit=60 * 60 - 120,
)
def import_archive_batches(self):
    """ANALYTICS app only: Celery beat calls this every few minutes to load new S3 batches."""
    from .importer import import_all
    from .s3 import ArchiveBucket

    if not cache.add(IMPORT_LOCK_KEY, self.request.id or "manual", 60 * 70):
        logger.info("[Archive Import] another import is running, skipping")
        return {"status": "locked"}
    try:
        return import_all(ArchiveBucket())
    finally:
        cache.delete(IMPORT_LOCK_KEY)
>>>>>>> upstream/dev-2.0
