"""
Management command: backfill_product_unique_code
==================================================

Purpose
-------
Assigns `Product.product_code` (a shop-unique, immutable, auto-incrementing
5-digit numeric identifier like "00001") to existing products, and keeps
each affected shop's `ShopNumberingSequence.last_product_number` counter
(the field `Product.save()` reads/increments for organic product creates)
in sync with whatever this command assigns. `Product.save()` only ever
generates a code for brand-new products, so every product created before
this field existed has `product_code=None` and needs a one-off backfill.

Note: `product_code` stores ONLY the zero-padded digits, never a prefix.
The displayed product code (e.g. "PRD00001") is assembled on read by
`Product.full_product_code`, which combines `product_code` with the shop's
`ShopNumberingSequence.product_code_prefix`. This command never touches
that prefix.

This command supports three composable options:
  1. Default (no flags): FULL REGENERATION. Every non-deleted product is
     renumbered from scratch, in per-shop creation order, starting at
     "00001" for each shop - even products that already have a code get
     overwritten. This is the actual difference from --missing-only below,
     not just a synonym for it.
  2. --shop_id: scopes either mode to a single shop. Composable with
     --missing-only (unlike the shop.backfill_shop_public_access command,
     where --shop_id and --missing-only are mutually exclusive).
  3. --missing-only: only assigns codes to products that don't have one yet.
     Existing codes are never touched, never renumbered, never overwritten.

Usage
-----
# Full regenerate for ALL shops (overwrites any existing codes):
    python manage.py backfill_product_unique_code

# Preview what would change, for any of the modes below, without writing:
    python manage.py backfill_product_unique_code --dry-run

# Full regenerate for ONE shop only:
    python manage.py backfill_product_unique_code --shop_id 45

# Fill in missing codes only, across ALL shops (existing codes untouched):
    python manage.py backfill_product_unique_code --missing-only

# Fill in missing codes only, for ONE shop:
    python manage.py backfill_product_unique_code --shop_id 45 --missing-only

How it works
-------------
1. Builds one query for the target products (all non-deleted, optionally
   scoped to --shop_id, optionally filtered to product_code IS NULL/empty
   for --missing-only), select_related("shop_service") + .only(...) so each
   product's shop_id is already loaded - no per-product query.
2. Orders that query by (shop_service__shop_id, created_at, id) so a single
   Python pass can assign sequential numbers per shop just by resetting a
   counter whenever the shop_id changes - no grouping, no per-shop queries.
3. For --missing-only, one extra aggregate query (grouped BY SHOP, not by
   product) finds each shop's current max code, so newly-assigned numbers
   continue from there instead of colliding with untouched existing codes.
   This lookup tolerates legacy-format product_code values (a leftover
   prefix, or a different digit count from before the current 5-digit-only
   format) by stripping non-digit characters before finding the max -
   requiring an exact format match here would silently exclude legacy rows
   and make a shop's numbering restart/collide instead of continuing from
   its true last number.
4. Writes with bulk_update() instead of per-row .save() - Product.save()'s
   own generation logic does a Shop row lock + (on first use per shop) a
   Max() query, which is correct for a single create request but would be
   an N+1 disaster (and pointless - a management command run offline has
   no concurrent writers to serialize against) across potentially thousands
   of existing products.
5. For any mode that can overwrite an already-coded row (i.e. NOT
   --missing-only), the affected rows are nulled out in one bulk_update
   pass before the real codes are written in a second pass. The
   unique_product_code_per_shop_service constraint is checked by Postgres
   at the end of each individual UPDATE statement against the WHOLE table,
   not just the rows that statement touched - so if a batch assigned a new
   code that happens to equal some other, not-yet-processed product's
   stale old code, that collision would exist on disk the instant the
   first batch's statement finishes and Postgres would raise
   IntegrityError. Nulling first sidesteps this entirely, since NULL is
   exempt from the constraint's condition. --missing-only never needs this
   pass - its target rows are already NULL by definition of the filter.
6. Product.save() reads/increments ShopNumberingSequence.last_product_number
   for organic creates, so this command - which bypasses .save() via
   bulk_update - must also keep that counter correct, or the next organic
   product created for a shop touched by this command would collide with
   (or leave a gap after) whatever this run just assigned. Each shop's
   final assigned number is tracked purely in memory while iterating (no
   extra per-product or per-shop query - it's just the running `counter`
   at the moment each product is processed), then synced to
   ShopNumberingSequence in exactly ONE bounded query + one bulk_create
   (for shops with no row yet) + one bulk_update after all Product writes
   succeed, inside the same transaction. This sync always OVERWRITES the
   stored value with the freshly computed one, even if the stored value is
   numerically higher (e.g. stale/incorrect data) - this command is an
   offline, authoritative backfill/regenerate with no concurrent writers to
   protect against (see point 4), so unlike Product.save()'s organic
   per-create increments, there is no "never move backward" concern here.

When to run
-----------
- Run ONCE per environment after deploying the product_code field/migration,
  to backfill all pre-existing products.
- Safe to re-run: default mode is deterministic (same product data in, same
  codes out) unless products were added/removed/reordered since the last
  run; --missing-only is always safe to re-run since it only ever fills gaps.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q, Max
from services.models import Product
from services.models.product import annotate_max_product_code_number

BATCH_SIZE = 500


class Command(BaseCommand):
    help = (
        "Backfill Product.product_code for existing products. By default, FULLY "
        "REGENERATES codes for ALL non-deleted products (overwrites existing codes). "
        "Use --shop_id to scope to a single shop, and/or --missing-only to only fill "
        "in products that don't have a code yet (never touching existing codes)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview affected products without writing to the database.",
        )
        parser.add_argument(
            "--shop_id",
            type=int,
            default=None,
            help="Scope the backfill to a single shop. Composable with --missing-only.",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Only assign codes to products that don't have one; never touch existing codes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        shop_id = options["shop_id"]
        missing_only = options["missing_only"]

        if shop_id is not None:
            from shop.models import Shop
            if not Shop.objects.filter(deleted__isnull=True, id=shop_id).exists():
                self.stderr.write(
                    self.style.ERROR(f"No non-deleted shop found with id={shop_id}.")
                )
                return

        qs = (
            Product.objects.filter(deleted__isnull=True)
            .select_related("shop_service")
            .only("id", "created_at", "product_code", "shop_service__shop_id")
            .order_by("shop_service__shop_id", "created_at", "id")
        )
        if shop_id is not None:
            qs = qs.filter(shop_service__shop_id=shop_id)
        if missing_only:
            qs = qs.filter(Q(product_code__isnull=True) | Q(product_code=""))

        # Missing-only mode must continue each shop's numbering from its current
        # max code, since already-coded products are excluded from `qs` above but
        # still occupy numbers. One aggregate query grouped by shop (O(#shops)
        # rows), not one query per shop or per product. Tolerates legacy-format
        # codes (see annotate_max_product_code_number) instead of silently
        # excluding them and restarting numbering from the wrong base.
        existing_max_by_shop = {}
        if missing_only:
            max_qs = Product.objects.filter(deleted__isnull=True)
            if shop_id is not None:
                max_qs = max_qs.filter(shop_service__shop_id=shop_id)
            existing_max_by_shop = dict(
                annotate_max_product_code_number(max_qs)
                .values("shop_service__shop_id")
                .annotate(max_code=Max("code_int"))
                .values_list("shop_service__shop_id", "max_code")
            )

        mode_label = "missing-only" if missing_only else "full-regenerate"
        scope_label = f", shop_id={shop_id}" if shop_id is not None else ", all shops"
        self.stdout.write(
            f"{'[DRY RUN] ' if dry_run else ''}Backfilling product codes "
            f"({mode_label}{scope_label})..."
        )

        # (product, new_code) pairs - new_code is kept separately (not just set on
        # the instance) so the full-regenerate null-out pass below can clear
        # product_code without losing track of the real value to restore.
        to_update = []
        shops_touched = set()
        # shop_id -> last counter value assigned during this run, tracked purely
        # in memory (no extra query) - recorded for EVERY product processed,
        # whether or not its own code needed writing, so it always reflects the
        # true final number for that shop, then written to Shop exactly once
        # after all Product writes succeed (never per-product/per-shop-transition).
        final_number_by_shop = {}
        current_shop_id = object()  # sentinel guaranteed to differ from any real shop id
        counter = 0

        for product in qs.iterator(chunk_size=2000):
            shop = product.shop_service.shop_id
            if shop != current_shop_id:
                current_shop_id = shop
                counter = existing_max_by_shop.get(shop, 0) if missing_only else 0

            counter += 1
            final_number_by_shop[shop] = counter
            new_code = f"{counter:05d}"

            if new_code == product.product_code:
                continue  # already correct - no write needed

            shops_touched.add(shop)
            if dry_run:
                self.stdout.write(
                    f"  [would update] product id={product.id} shop_id={shop} "
                    f"'{product.product_code}' -> '{new_code}'"
                )
                continue

            to_update.append((product, new_code))

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DRY RUN] would update products across {len(shops_touched)} shop(s) "
                    f"(and sync last_product_number for {len(final_number_by_shop)} shop(s))."
                )
            )
            return

        if not to_update and not final_number_by_shop:
            self.stdout.write(self.style.SUCCESS("Nothing to do - no matching products found."))
            return

        with transaction.atomic():
            if to_update:
                products = [product for product, _new_code in to_update]

                if not missing_only:
                    # Null out first: sidesteps the unique_product_code_per_shop_service
                    # constraint (exempt for NULL), so a stale old code left in a
                    # not-yet-processed batch can never collide with a new code just
                    # written by an earlier batch.
                    for product in products:
                        product.product_code = None
                    Product.objects.bulk_update(products, ["product_code"], batch_size=BATCH_SIZE)

                # Write the real, freshly computed codes.
                for product, new_code in to_update:
                    product.product_code = new_code
                Product.objects.bulk_update(products, ["product_code"], batch_size=BATCH_SIZE)

            # Sync ShopNumberingSequence.last_product_number exactly once per
            # shop, after all Product writes have succeeded - one bounded
            # query + one bulk_create (for shops with no row yet) + one
            # bulk_update, never a per-product or per-shop-transition write.
            # This backfill is authoritative and offline (no concurrent
            # writers to serialize against - see §4 above), so the computed
            # final number always OVERWRITES whatever was previously stored,
            # even if that stored value happens to be numerically higher
            # (e.g. stale/incorrect data from before a re-run) - unlike
            # Product.save()'s organic per-create increments, there is no
            # "don't move backward" concern here: the freshly computed value
            # IS the correct one. computed == existing is skipped purely as
            # a no-op write optimization, not a guard.
            shops_synced = 0
            if final_number_by_shop:
                from shop.models import ShopNumberingSequence
                sequences = list(
                    ShopNumberingSequence.objects.filter(shop_id__in=final_number_by_shop.keys())
                    .only("id", "shop_id", "last_product_number")
                )
                sequences_by_shop = {seq.shop_id: seq for seq in sequences}

                to_create = [
                    ShopNumberingSequence(shop_id=shop_id, last_product_number=computed)
                    for shop_id, computed in final_number_by_shop.items()
                    if shop_id not in sequences_by_shop
                ]
                if to_create:
                    ShopNumberingSequence.objects.bulk_create(to_create, batch_size=BATCH_SIZE)

                to_bump = []
                for shop_id, seq in sequences_by_shop.items():
                    computed = final_number_by_shop[shop_id]
                    if computed != seq.last_product_number:
                        seq.last_product_number = computed
                        to_bump.append(seq)
                if to_bump:
                    ShopNumberingSequence.objects.bulk_update(to_bump, ["last_product_number"], batch_size=BATCH_SIZE)

                shops_synced = len(to_create) + len(to_bump)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {len(to_update)} product(s) updated across {len(shops_touched)} shop(s); "
                f"last_product_number synced for {shops_synced} shop(s)."
            )
        )
