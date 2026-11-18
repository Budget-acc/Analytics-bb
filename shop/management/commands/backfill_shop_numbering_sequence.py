"""
Management command: backfill_shop_numbering_sequence
======================================================

Purpose
-------
Copies each Shop's numbering/counter fields into the new, separate
`ShopNumberingSequence` table (one row per Shop, linked via a OneToOne
`shop` field). This is a data-copy step only, in preparation for eventually
removing these fields from `Shop` in a later, separate change.

New model/table
----------------
`shop.models.ShopNumberingSequence` (table `shop_shopnumberingsequence`).

Fields copied (Shop field -> ShopNumberingSequence field, same names)
-----------------------------------------------------------------------
    booking_start_id, last_booking_id,
    sale_start_id, last_sale_id,
    tailor_start_id, last_tailor_id,
    purchase_start_id, last_purchase_id,
    last_product_number

This command does NOT touch, modify, or remove any of these fields on
`Shop` — it only reads them and writes matching values into
`ShopNumberingSequence`. `Shop` remains the source of truth for all
existing read/write logic (Product.save(), Booking/Sale/TailorOrder/
Purchase counters, etc.) until a later change rewires those to use the new
table and drops the old Shop fields.

This command supports four use cases, controlled by `--shop_ids` and
`--missing-only` (composable, not mutually exclusive):

  1. Copy all missing Shop details (no existing --shop_ids scope):
         python manage.py backfill_shop_numbering_sequence --missing-only
     Only shops without an existing ShopNumberingSequence row get one
     created. Shops that already have a row are left untouched.

  2. Copy/upsert ALL Shop details:
         python manage.py backfill_shop_numbering_sequence
     Every non-deleted shop gets a ShopNumberingSequence row: created if
     missing, or updated (overwritten with current Shop values) if it
     already exists.

  3. Copy details for specified Shops (upsert):
         python manage.py backfill_shop_numbering_sequence --shop_ids 12,45,78
     Scopes case (2) to just the given shop IDs (comma-separated, no
     spaces). Existing rows for those shops are updated; missing ones are
     created.

  4. Copy missing details for specified Shops:
         python manage.py backfill_shop_numbering_sequence --shop_ids 12,45,78 --missing-only
     Scopes case (1) to just the given shop IDs — only those without an
     existing row get one created; shops in the list that already have a
     row are skipped.

`--dry-run` composes with all four modes above and previews the affected
shops without writing to the database.

Usage
-----
# Copy all missing shop details (safe default for routine re-runs):
    python manage.py backfill_shop_numbering_sequence --missing-only

# Copy/upsert ALL shops (creates missing rows, refreshes existing ones):
    python manage.py backfill_shop_numbering_sequence

# Preview any of the above without writing:
    python manage.py backfill_shop_numbering_sequence --dry-run
    python manage.py backfill_shop_numbering_sequence --missing-only --dry-run

# Scope to specific shops (comma-separated IDs, upsert):
    python manage.py backfill_shop_numbering_sequence --shop_ids 12,45,78

# Scope to specific shops, missing-only:
    python manage.py backfill_shop_numbering_sequence --shop_ids 12,45,78 --missing-only

How it works (bulk, no N+1 queries)
------------------------------------
1. Parses `--shop_ids` (comma-separated ints) if given, and validates that
   every ID corresponds to an existing non-deleted Shop before doing
   anything else — if any ID is invalid, the command errors out and makes
   no writes at all.
2. Builds ONE Shop queryset (`deleted__isnull=True`, optionally filtered to
   `id__in=shop_ids`), `.only()`-ing just the 9 source fields + `id` so no
   other Shop columns are fetched.
3. Runs ONE query to find which of the candidate shops already have a
   ShopNumberingSequence row (`values_list("shop_id", flat=True)`). For
   `--missing-only`, those shop IDs are excluded from the Shop queryset
   up front so only genuinely-missing shops are processed at all.
4. Iterates the (already bounded, single) Shop queryset once in Python,
   building one ShopNumberingSequence instance per shop in memory — no
   per-shop queries.
5. Writes everything in a single `bulk_create(..., update_conflicts=True,
   unique_fields=["shop"], update_fields=[...])` call (Django 4.1+ upsert),
   batched via `batch_size`, inside one `transaction.atomic()`. This one
   call transparently creates missing rows and updates existing ones
   without any separate per-row `.save()` or `get_or_create()` — no N+1.

Safety / idempotency
---------------------
- Re-running any mode is always safe: the OneToOne `shop` field is the
  natural upsert key, so no duplicate ShopNumberingSequence rows are ever
  created.
- Re-running with unchanged Shop data writes the same values again
  (harmless no-op in effect, though `updated_at` will still bump).
- `--missing-only` is always the most conservative option: it only ever
  creates new rows, never modifies an existing one.
- The full write phase runs inside one `transaction.atomic()` block: a
  failure partway through rolls back cleanly with no partial writes.
- `--shop_ids` validation happens before any writes: if any given ID does
  not match a non-deleted Shop, the command reports the invalid IDs and
  exits without touching the database.
- `--dry-run` never writes; it only prints what would happen.

When to run / production recommendation
------------------------------------------
- Run once per environment after the `ShopNumberingSequence` migration has
  been applied.
- Always run with `--dry-run` first (optionally combined with
  `--missing-only` and/or `--shop_ids`) to review the exact set of shops
  that will be created/updated before writing.
- For a first production rollout, prefer plain `--missing-only` (safe,
  additive-only) over the upsert-all default. Use the no-flag upsert-all
  form only when you deliberately want to refresh already-copied rows to
  match the latest Shop values (e.g. after Shop data changed post-copy).
- Safe to re-run at any time; it is idempotent with respect to current
  Shop data.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from shop.models import Shop, ShopNumberingSequence

BATCH_SIZE = 500

COPIED_FIELDS = [
    "booking_start_id",
    "last_booking_id",
    "sale_start_id",
    "last_sale_id",
    "tailor_start_id",
    "last_tailor_id",
    "purchase_start_id",
    "last_purchase_id",
    "last_product_number",
]


class Command(BaseCommand):
    help = (
        "Copy Shop numbering/counter fields into the new ShopNumberingSequence "
        "table. By default upserts ALL non-deleted shops (creates missing rows, "
        "updates existing ones). Use --missing-only to only create rows for shops "
        "that don't have one yet, and/or --shop_ids to scope to specific shops. "
        "Does not modify or remove any fields on Shop. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview affected shops without writing to the database.",
        )
        parser.add_argument(
            "--shop_ids",
            type=str,
            default=None,
            help="Comma-separated list of shop IDs to scope the copy to, e.g. 12,45,78.",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Only create rows for shops without an existing ShopNumberingSequence; "
                 "never update existing rows.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        missing_only = options["missing_only"]
        shop_ids_raw = options["shop_ids"]

        shop_ids = None
        if shop_ids_raw:
            try:
                shop_ids = [int(part.strip()) for part in shop_ids_raw.split(",") if part.strip()]
            except ValueError:
                self.stderr.write(
                    self.style.ERROR(f"--shop_ids must be a comma-separated list of integers, got: {shop_ids_raw!r}")
                )
                return

            if not shop_ids:
                self.stderr.write(self.style.ERROR("--shop_ids must contain at least one shop ID."))
                return

            found_ids = set(
                Shop.objects.filter(deleted__isnull=True, id__in=shop_ids).values_list("id", flat=True)
            )
            missing_ids = sorted(set(shop_ids) - found_ids)
            if missing_ids:
                self.stderr.write(
                    self.style.ERROR(
                        f"No non-deleted shop found for id(s): {', '.join(str(i) for i in missing_ids)}."
                    )
                )
                return

        qs = Shop.objects.filter(deleted__isnull=True).only("id", *COPIED_FIELDS)
        if shop_ids is not None:
            qs = qs.filter(id__in=shop_ids)

        candidate_shop_ids = list(qs.values_list("id", flat=True))
        existing_shop_ids = set(
            ShopNumberingSequence.objects.filter(shop_id__in=candidate_shop_ids)
            .values_list("shop_id", flat=True)
        )

        if missing_only:
            qs = qs.exclude(id__in=existing_shop_ids)

        qs = qs.order_by("id")
        total = qs.count()

        mode_label = "missing-only" if missing_only else "upsert-all"
        scope_label = f", shop_ids={shop_ids}" if shop_ids is not None else ", all shops"
        self.stdout.write(
            f"{'[DRY RUN] ' if dry_run else ''}Copying shop numbering sequence "
            f"({mode_label}{scope_label})..."
        )

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No matching shops found. Nothing to do."))
            return

        to_upsert = []
        creates = 0
        updates = 0

        for shop in qs.iterator(chunk_size=2000):
            is_update = shop.id in existing_shop_ids
            if is_update:
                updates += 1
            else:
                creates += 1

            if dry_run:
                action = "would update" if is_update else "would create"
                self.stdout.write(f"  [{action}] shop id={shop.id}")
                continue

            to_upsert.append(
                ShopNumberingSequence(
                    shop_id=shop.id,
                    **{field: getattr(shop, field) for field in COPIED_FIELDS},
                )
            )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DRY RUN] would create {creates} and update {updates} "
                    f"ShopNumberingSequence row(s), {total} shop(s) total."
                )
            )
            return

        with transaction.atomic():
            ShopNumberingSequence.objects.bulk_create(
                to_upsert,
                batch_size=BATCH_SIZE,
                update_conflicts=True,
                unique_fields=["shop"],
                update_fields=COPIED_FIELDS,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {creates} row(s) created, {updates} row(s) updated, "
                f"{total} shop(s) total."
            )
        )
