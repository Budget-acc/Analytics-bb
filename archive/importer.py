"""
ANALYTICS app side: load the batches the live app wrote to S3.

For every S3 folder that has a manifest.json and is not loaded yet:
  1. Create an ImportBatch row (status "loading").
  2. In ONE transaction, for each table in manifest "load_order" (parents first):
       - download the file, check its sha256 and row count against the manifest
       - insert or update every row with the SAME id as in live
       - write one ImportTableLog row (inserted / updated / dates / total)
  3. All good -> status "loaded". Any problem -> everything rolls back,
     status "failed" with the error, and the next run tries again.

Model save() methods and signals are switched off while loading, so nothing
in the analytics app recalculates, numbers or logs the copied rows again.
"""

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from datetime import date

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.db import transaction
from django.db.models import signals
from django.utils import timezone

from .collect import all_rows
from .models import ImportBatch, ImportTableLog

logger = logging.getLogger("api_requests")

_MUTED_SIGNALS = [
    signals.pre_save, signals.post_save,
    signals.pre_delete, signals.post_delete,
    signals.m2m_changed,
]
_KEY_ORDER = re.compile(r"run=(\d+)_[^/]*/batch=(\d+)_")

# Users the analytics app creates for itself (its own admin logins) use IDs from
# this number up. Imported rows never touch them, so an import can't overwrite them.
LOCAL_USER_ID_START = 900_000_000


class ImportFailed(Exception):
    pass


@contextmanager
def muted_signals():
    """Turn off model signals (activity logs, ledger hooks, ...) while copying rows."""
    saved = [(s, s.receivers[:]) for s in _MUTED_SIGNALS]
    try:
        for s, _ in saved:
            s.receivers = []
            s.sender_receivers_cache.clear()
        yield
    finally:
        for s, receivers in saved:
            s.receivers = receivers
            s.sender_receivers_cache.clear()


def import_prefix():
    return getattr(settings, "ARCHIVE_IMPORT_PREFIX", None) or getattr(settings, "ARCHIVE_S3_PREFIX", "booking-archive/v1/")


def _sort_key(manifest_key):
    m = _KEY_ORDER.search(manifest_key)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def pending_manifests(bucket):
    """manifest.json keys not loaded yet, oldest run / batch first."""
    loaded = set(ImportBatch.objects.filter(status=ImportBatch.Status.LOADED).values_list("s3_prefix", flat=True))
    keys = [k for k in bucket.list_manifests(import_prefix()) if k[: -len("manifest.json")] not in loaded]
    return sorted(keys, key=_sort_key)


def _as_date(value):
    return date.fromisoformat(value) if value else None


def import_batch(bucket, manifest_key):
    prefix = manifest_key[: -len("manifest.json")]
    manifest = json.loads(bucket.get(manifest_key))

    batch, _ = ImportBatch.objects.update_or_create(
        s3_prefix=prefix,
        defaults={
            "run_id": manifest["run_id"],
            "batch_id": manifest["batch_id"],
            "shop_id": manifest["shop_id"],
            "kind": manifest["kind"],
            "status": ImportBatch.Status.LOADING,
            "data_from": _as_date(manifest.get("data_from")),
            "data_to": _as_date(manifest.get("data_to")),
            "error": "",
            "started_at": timezone.now(),
            "finished_at": None,
        },
    )

    try:
        with transaction.atomic(), muted_signals():
            batch.tables.all().delete()
            total = 0

            for table in manifest["load_order"]:
                info = manifest["tables"][table]
                body = bucket.get(f"{prefix}{table}.json")
                if hashlib.sha256(body).hexdigest() != info["sha256"]:
                    raise ImportFailed(f"{table}: checksum does not match manifest.json")

                model = apps.get_model(info["model"])
                objs = list(serializers.deserialize("json", body, ignorenonexistent=True))
                if len(objs) != info["rows"]:
                    raise ImportFailed(f"{table}: file has {len(objs)} rows, manifest says {info['rows']}")

                skipped = 0
                if model._meta.label_lower == settings.AUTH_USER_MODEL.lower():
                    kept = [o for o in objs if o.object.pk < LOCAL_USER_ID_START]
                    skipped, objs = len(objs) - len(kept), kept

                pks = [o.object.pk for o in objs]
                existing = set(all_rows(model).filter(pk__in=pks).values_list("pk", flat=True))
                for obj in objs:
                    obj.save()  # same id as live; updates the row if it is already here

                ImportTableLog.objects.create(
                    import_batch=batch,
                    table_name=table,
                    rows_in_file=info["rows"],
                    rows_inserted=len(objs) - len(existing),
                    rows_updated=len(existing),
                    rows_failed=skipped,
                    date_from=_as_date(info.get("date_from")),
                    date_to=_as_date(info.get("date_to")),
                    total_amount=info.get("total_amount"),
                    checksum_ok=True,
                )
                total += len(objs)

            batch.status = ImportBatch.Status.LOADED
            batch.total_rows = total
            batch.finished_at = timezone.now()
            batch.save(update_fields=["status", "total_rows", "finished_at"])

    except Exception as exc:
        logger.error("[Archive Import] %s failed: %s", prefix, exc, exc_info=True)
        ImportBatch.objects.filter(id=batch.id).update(
            status=ImportBatch.Status.FAILED, error=str(exc)[:4000], finished_at=timezone.now()
        )
        return batch.id, False

    logger.info("[Archive Import] %s loaded (%s rows)", prefix, total)
    return batch.id, True


def import_all(bucket, limit=None):
    """Load every pending batch. Stops at the first failure so the order stays correct."""
    done, failed = 0, 0
    for key in pending_manifests(bucket)[:limit]:
        _, ok = import_batch(bucket, key)
        if ok:
            done += 1
        else:
            failed += 1
            break
    return {"loaded": done, "failed": failed}