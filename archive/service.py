"""
The nightly job. For each shop in ARCHIVE_SHOP_IDS:

    bookings -> sales -> expenses -> product expenses   (batches of ~500)
    then, only if all of them succeeded: daily balance rows

Each batch: collect -> upload to S3 -> verify in S3 -> delete from live.
"""

import logging

from django.conf import settings
from django.utils import timezone

from . import config
from .collect import collect, pick_ids
from .export import build_prefix, export_bundle, verify_upload
from .models import ArchiveBatch, ArchiveRun
from .purge import purge_batch
from .s3 import ArchiveBucket

logger = logging.getLogger("api_requests")


def purge_enabled():
    return getattr(settings, "BOOKING_ARCHIVE_PURGE_ENABLED", False)


def run_archive(dry_run=False, shop_ids=None, cutoff=None):
    cutoff = cutoff or config.archive_cutoff()
    allowed = config.archive_shop_ids()
    if shop_ids is None:
        shop_ids = allowed
    else:
        # A shop given on the command line must also be in ARCHIVE_SHOP_IDS.
        not_allowed = [s for s in shop_ids if s not in allowed]
        if not_allowed:
            raise ValueError(f"Shop(s) {not_allowed} are not in ARCHIVE_SHOP_IDS")
    shop_ids = sorted(set(shop_ids))

    run = ArchiveRun.objects.create(cutoff=cutoff, shop_ids=shop_ids, dry_run=dry_run)
    bucket = None if dry_run else ArchiveBucket()
    summary, any_failed = {}, False

    for shop_id in shop_ids:
        shop_ok, shop_summary = True, {}
        for kind in config.KINDS:
            ok, n = _archive_kind(run, bucket, shop_id, kind, cutoff, dry_run)
            shop_summary[kind] = n
            if not ok:
                shop_ok = False
                break
        if shop_ok:
            ok, n = _archive_kind(run, bucket, shop_id, config.DAILY_BALANCE_KIND, cutoff, dry_run)
            shop_summary[config.DAILY_BALANCE_KIND] = n
            shop_ok = ok
        any_failed |= not shop_ok
        summary[str(shop_id)] = {"ok": shop_ok, "batches": shop_summary}

    run.summary = summary
    run.status = ArchiveRun.Status.FAILED if any_failed else ArchiveRun.Status.FINISHED
    run.finished_at = timezone.now()
    run.save(update_fields=["summary", "status", "finished_at"])
    logger.info("[Archive] run=%s finished status=%s summary=%s", run.id, run.status, summary)
    return run


def _archive_kind(run, bucket, shop_id, kind, cutoff, dry_run):
    """Returns (all_ok, batches_done)."""
    after_id, done = 0, 0
    while True:
        ids = pick_ids(kind, shop_id, cutoff, after_id)
        if not ids:
            return True, done
        after_id = ids[-1]

        batch = ArchiveBatch.objects.create(run=run, shop_id=shop_id, kind=kind, record_ids=ids)
        try:
            _process_batch(batch, bucket, cutoff, dry_run)
            done += 1
        except Exception as exc:
            logger.error("[Archive] batch=%s shop=%s kind=%s failed: %s", batch.id, shop_id, kind, exc, exc_info=True)
            ArchiveBatch.objects.filter(id=batch.id).update(
                status=ArchiveBatch.Status.FAILED, error=str(exc)[:4000]
            )
            return False, done  # stop this shop; the rest is retried next night


def _process_batch(batch, bucket, cutoff, dry_run):
    # 1-2. pick + collect
    bundle = collect(batch.kind, batch.shop_id, batch.record_ids)
    batch.moved_ids = bundle.moved_ids()
    batch.manifest_counts = bundle.counts()

    if dry_run:
        batch.status = ArchiveBatch.Status.DRY_RUN
        batch.s3_prefix = build_prefix(batch)
        batch.save(update_fields=["moved_ids", "manifest_counts", "status", "s3_prefix"])
        return

    # 3. upload (manifest.json last)
    prefix, manifest = export_bundle(bucket, batch, bundle, cutoff)
    batch.s3_prefix = prefix
    batch.status = ArchiveBatch.Status.UPLOADED
    batch.uploaded_at = timezone.now()
    batch.save(update_fields=["moved_ids", "manifest_counts", "s3_prefix", "status", "uploaded_at"])

    # 4. verify what is in S3
    verify_upload(bucket, prefix, manifest)
    batch.status = ArchiveBatch.Status.VERIFIED
    batch.verified_at = timezone.now()
    batch.save(update_fields=["status", "verified_at"])

    # 5. delete from live (off until BOOKING_ARCHIVE_PURGE_ENABLED = true)
    if purge_enabled():
        purge_batch(batch, bundle, cutoff)