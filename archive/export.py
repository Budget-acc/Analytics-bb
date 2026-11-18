"""
Step 3 and 4 of a batch: write one JSON file per table to S3, write
manifest.json LAST, then download everything again and check it.
"""

import hashlib
import json
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from . import config

MANIFEST_VERSION = 1


class VerifyFailed(Exception):
    """What is in S3 does not match what we exported. Nothing is deleted."""


def build_prefix(batch):
    base = getattr(settings, "ARCHIVE_S3_PREFIX", "booking-archive/v1/")
    run = batch.run
    return (
        f"{base}shop={batch.shop_id}/"
        f"run={run.id}_{timezone.localtime(run.created_at):%Y-%m-%d}/"
        f"batch={batch.id}_{batch.kind}/"
    )


def load_order(models):
    """Parents before children, so the analytics app can insert in this order."""
    models = set(models)
    order, done = [], set()

    def visit(model, stack):
        if model in done or model in stack:
            return  # already placed, or a cycle (nullable FKs; Postgres checks FKs at COMMIT)
        stack.add(model)
        for field in model._meta.concrete_fields:
            if field.is_relation and (field.many_to_one or field.one_to_one):
                target = field.related_model._meta.concrete_model
                if target in models and target is not model:
                    visit(target, stack)
        stack.discard(model)
        done.add(model)
        order.append(model)

    for model in sorted(models, key=lambda m: m._meta.db_table):
        visit(model, set())
    return order


def _serialize(model, objs):
    data = serializers.serialize("python", objs)
    m2m = {f.name for f in model._meta.many_to_many}
    ct_fields = {
        f.name for f in model._meta.concrete_fields
        if f.is_relation and f.related_model is ContentType
    }
    is_user = model._meta.label_lower == settings.AUTH_USER_MODEL.lower()

    for row in data:
        fields = row["fields"]
        for name in m2m:
            fields.pop(name, None)  # M2M targets are not exported; avoids broken links on load
        for name in ct_fields:
            if fields.get(name):
                ct = ContentType.objects.get_for_id(fields[name])
                fields[name] = [ct.app_label, ct.model]  # natural key: IDs can differ between DBs
<<<<<<< HEAD
        if is_user and "password" in fields:
            fields["password"] = "!"  # never copy password hashes to the analytics app
=======
        if is_user:
            # never copy password hashes to the analytics app
            for secret in ("password", "secondary_password"):
                if secret in fields:
                    fields[secret] = "!"
>>>>>>> upstream/dev-2.0
    return json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False).encode("utf-8")


def _dates_and_total(model, objs):
    table = model._meta.db_table
    date_field, amount_field = config.DATE_FIELD.get(table), config.AMOUNT_FIELD.get(table)
    dates, total = [], Decimal("0")
    for obj in objs:
        if date_field:
            value = getattr(obj, date_field, None)
            if value is not None:
                dates.append(timezone.localtime(value).date() if hasattr(value, "tzinfo") and value.tzinfo else value)
        if amount_field:
            amount = getattr(obj, amount_field, None)
            if amount is not None:
                total += Decimal(str(amount))
    return (
        min(dates).isoformat() if dates else None,
        max(dates).isoformat() if dates else None,
        str(total) if amount_field else None,
    )


def export_bundle(bucket, batch, bundle, cutoff):
    prefix = build_prefix(batch)
    order = load_order(bundle.all_models())
    tables = {}
    all_dates = []

    for model in order:
        table = model._meta.db_table
        objs = bundle.rows_for(model)
        body = _serialize(model, objs)
        bucket.put(f"{prefix}{table}.json", body)

        moved_objs = list(bundle.moved.get(model, {}).values())
        date_from, date_to, total = _dates_and_total(model, moved_objs)
        if date_from:
            all_dates += [date_from, date_to]
        tables[table] = {
            "model": model._meta.label_lower,
            "rows": len(objs),
            "moved": len(moved_objs),
            "sha256": hashlib.sha256(body).hexdigest(),
            "date_from": date_from,
            "date_to": date_to,
            "total_amount": total,
        }

    manifest = {
        "version": MANIFEST_VERSION,
        "run_id": batch.run_id,
        "batch_id": batch.id,
        "shop_id": batch.shop_id,
        "kind": batch.kind,
        "cutoff": cutoff.isoformat(),
        "created_at": timezone.now().isoformat(),
        "data_from": min(all_dates) if all_dates else None,
        "data_to": max(all_dates) if all_dates else None,
        "load_order": [m._meta.db_table for m in order],
        "tables": tables,
    }
    # Written LAST: a folder without manifest.json is an unfinished upload.
    bucket.put(f"{prefix}manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    return prefix, manifest


def verify_upload(bucket, prefix, manifest):
    """Download every file again and compare row counts and checksums."""
    stored = json.loads(bucket.get(f"{prefix}manifest.json"))
    if stored.get("tables") != manifest["tables"]:
        raise VerifyFailed("manifest.json in S3 differs from the one we wrote")

    for table, info in manifest["tables"].items():
        body = bucket.get(f"{prefix}{table}.json")
        if hashlib.sha256(body).hexdigest() != info["sha256"]:
            raise VerifyFailed(f"{table}: checksum mismatch")
        if len(json.loads(body)) != info["rows"]:
            raise VerifyFailed(f"{table}: row count mismatch")