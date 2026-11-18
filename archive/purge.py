"""
Step 5 of a batch: delete the moved rows from the live database.

Runs in ONE transaction per batch:
  1. Lock the main rows and check they still pass the archive rule.
  2. Check that no live row still points at a row we are about to delete
     (for example a payment added after export). If one does, stop.
  3. Set a few known links to NULL (converted tailor order, restored log).
  4. Hard-delete children first, then main rows (raw SQL, so safedelete
     soft-delete and signals are skipped: update_daily_balance is NOT called).
  5. Delete clients that nothing uses any more.
  6. After COMMIT, remove the booking documents from the live S3 bucket.
"""

import json
import logging

from django.db import connection, transaction
from django.utils import timezone

from . import config
from .collect import all_rows, eligible_queryset, model_for_table
from .export import load_order

logger = logging.getLogger("api_requests")


class PurgeAborted(Exception):
    """The batch must not be deleted. The transaction is rolled back; nothing changes."""


# Plain "IN (...)" SQL works on PostgreSQL (dev / prod) and SQLite (local).
# Lists are sent in chunks so SQLite's limit on query parameters is never hit.
SQL_CHUNK = 500


def _chunks(values):
    values = list(values)
    for i in range(0, len(values), SQL_CHUNK):
        yield values[i:i + SQL_CHUNK]


def _in_list(values):
    return "(" + ", ".join(["%s"] * len(values)) + ")"


def _check_still_eligible(batch, cutoff):
    main_model = model_for_table(config.MAIN_TABLE[batch.kind])
    ids = list(batch.record_ids)
    # Lock the rows so nothing changes them while we delete.
    list(all_rows(main_model).filter(id__in=ids).select_for_update().values_list("id", flat=True))
    still = set(eligible_queryset(batch.kind, batch.shop_id, cutoff).filter(id__in=ids).values_list("id", flat=True))
    changed = sorted(set(ids) - still)
    if changed:
        raise PurgeAborted(f"{len(changed)} record(s) changed after export, e.g. {changed[:10]}. Retry next night.")


def _check_no_live_references(bundle):
    """No row that stays in live may point at a row we delete."""
    problems = []
    for model, rows in bundle.moved.items():
        ids = list(rows)
        for rel in model._meta.related_objects:
            src = rel.related_model._meta.concrete_model
            if rel.many_to_many:
                through = getattr(rel, "through", None) or rel.field.remote_field.through
                col = rel.field.m2m_reverse_field_name()
                if through._default_manager.filter(**{f"{col}__in": ids}).exists():
                    problems.append(f"M2M {through._meta.db_table} -> {model._meta.db_table}")
                continue
            if (src._meta.db_table, rel.field.column) in config.NULLIFY_REFERENCES:
                continue
            if not rel.field.concrete:
                continue
            leftover = all_rows(src).filter(**{f"{rel.field.attname}__in": ids})
            moved_src = bundle.moved.get(src)
            if moved_src:
                leftover = leftover.exclude(pk__in=list(moved_src))
            count = leftover.count()
            if count:
                problems.append(f"{count} row(s) in {src._meta.db_table}.{rel.field.column}")
    if problems:
        raise PurgeAborted("Live rows still point at rows in this batch: " + "; ".join(problems))


def _nullify_links(cursor, bundle):
    moved_by_table = {m._meta.db_table: list(rows) for m, rows in bundle.moved.items()}
    for src_table, column in config.NULLIFY_REFERENCES:
        try:
            src_model = model_for_table(src_table)
        except LookupError:
            continue
        field = next((f for f in src_model._meta.concrete_fields if f.column == column), None)
        if field is None:
            continue
        target_ids = moved_by_table.get(field.related_model._meta.db_table)
        for chunk in _chunks(target_ids or []):
            cursor.execute(
                f'UPDATE "{src_table}" SET "{column}" = NULL WHERE "{column}" IN {_in_list(chunk)}',
                chunk,
            )


def _delete_orphan_clients(cursor, bundle):
    deleted = {}
    for model, rows in bundle.refs.items():
        table = model._meta.db_table
        if table not in config.CLIENT_TABLES or not rows:
            continue
        clauses = []
        for rel in model._meta.related_objects:
            if rel.many_to_many:
                return deleted  # be safe: never delete clients that have M2M links
            src = rel.related_model._meta.db_table
            clauses.append(f'NOT EXISTS (SELECT 1 FROM "{src}" r WHERE r."{rel.field.column}" = c.id)')
        where = (" AND " + " AND ".join(clauses)) if clauses else ""
        n = 0
        for chunk in _chunks(rows):
            cursor.execute(
                f'DELETE FROM "{table}" AS c WHERE c.id IN {_in_list(chunk)}{where}',
                chunk,
            )
            n += cursor.rowcount
        if n:
            deleted[table] = n
    return deleted


def _booking_documents(bundle):
    booking_model = model_for_table("booking_v2_bookingv2")
    urls = []
    for obj in bundle.moved.get(booking_model, {}).values():
        value = getattr(obj, "documents", None)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = None
        if isinstance(value, list):
            urls += [u for u in value if isinstance(u, str) and u]
    return urls


def purge_batch(batch, bundle, cutoff):
    from .models import ArchiveBatch

    documents = _booking_documents(bundle)
    counts = {}

    with transaction.atomic():
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                # Never hold locks long enough to slow down the live app (PostgreSQL only).
                cursor.execute(f"SET LOCAL lock_timeout = '{config.LOCK_TIMEOUT}'")
                cursor.execute(f"SET LOCAL statement_timeout = '{config.STATEMENT_TIMEOUT}'")

            _check_still_eligible(batch, cutoff)
            _check_no_live_references(bundle)
            _nullify_links(cursor, bundle)

            # Children first: reverse of the parent-first load order.
            for model in reversed(load_order(bundle.moved.keys())):
                ids = list(bundle.moved[model])
                table, pk = model._meta.db_table, model._meta.pk.column
                deleted = 0
                for chunk in _chunks(ids):
                    cursor.execute(f'DELETE FROM "{table}" WHERE "{pk}" IN {_in_list(chunk)}', chunk)
                    deleted += cursor.rowcount
                if deleted != len(ids):
                    raise PurgeAborted(f"{table}: expected to delete {len(ids)} rows, deleted {deleted}")
                counts[table] = deleted

            counts.update(_delete_orphan_clients(cursor, bundle))

        batch.status = ArchiveBatch.Status.PURGED
        batch.purge_counts = counts
        batch.purged_at = timezone.now()
        batch.save(update_fields=["status", "purge_counts", "purged_at"])

        if documents:
            def _cleanup_live_s3():
                try:
                    # Existing task: checks each URL belongs to the live bucket, then deletes it.
                    from tailored.tasks.document_tasks import cleanup_s3_tailor_order_documents_task
                    cleanup_s3_tailor_order_documents_task.apply_async(args=[documents])
                except Exception:
                    logger.exception("[Archive] could not queue S3 document cleanup for batch %s", batch.id)

            transaction.on_commit(_cleanup_live_s3)

    return counts