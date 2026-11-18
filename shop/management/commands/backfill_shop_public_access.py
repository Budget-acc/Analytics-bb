"""
Management command: backfill_shop_public_access
================================================

Purpose
-------
Ensures every non-deleted Shop has:
  - A URL-safe `slug`  (generated from shop name via Django's slugify)
  - A unique 12-character `public_token` (collision-safe nanoid)

These two fields power the public shop catalog API and QR-code URLs.

This command handles three scenarios:
  1. New shops that have never had a token (first-time backfill).
  2. Existing shops that still carry the old 10-character token format —
     all such tokens are regenerated to the current 12-character format
     so that the public API can correctly identify them as Shop tokens
     (Organisation tokens remain 10 chars, so length is the discriminator).
  3. Forced regeneration for a single shop, or a strict missing-only pass —
     see `--shop_id` and `--missing-only` below.

Usage
-----
# Standard run (writes to DB, regenerates missing-or-outdated tokens for ALL shops):
    python manage.py backfill_shop_public_access

# Preview affected shops without making any DB changes:
    python manage.py backfill_shop_public_access --dry-run

# Force-regenerate the token for one shop (overwrites any existing token):
    python manage.py backfill_shop_public_access --shop_id 45

# Backfill only shops with a missing/empty token, skip all others
# (even shops with an old 10-char token are left untouched):
    python manage.py backfill_shop_public_access --missing-only

`--shop_id` and `--missing-only` are mutually exclusive. Both compose with `--dry-run`.

How it works
------------
1. Queries non-deleted shops (all of them, or just the one matching `--shop_id`).
2. For each shop:
   - Generates `slug` via Django's `slugify(shop.name)` if missing.
   - Decides whether the token needs (re)generation:
       * `--shop_id` mode: always regenerates (forced), regardless of current token.
       * `--missing-only` mode: only if the token is missing/empty.
       * default mode: if missing OR not exactly 12 characters (catches legacy 10-char tokens).
   - Saves only the changed fields using `update_fields`.
3. Prints a per-shop summary and a final count.

When to run
-----------
- Run ONCE on each environment (staging, production) after deploying the
  token-length change (Shop tokens: 10 → 12 chars).
- Safe to re-run: shops that already have a slug AND a 12-char token are
  skipped with no DB writes (unless `--shop_id` forces it).
"""

from django.core.management.base import BaseCommand
from django.utils.text import slugify
from shop.models import Shop
from shop.models.shop import generate_unique_public_token


class Command(BaseCommand):
    help = (
        "Backfill / refresh slug and public_token for shops. "
        "By default regenerates tokens that are missing or not 12 characters (old 10-char format) "
        "for ALL shops. Use --shop_id to force-regenerate a single shop's token, or --missing-only "
        "to only fill in tokens that are completely absent. Safe to re-run — shops already "
        "up-to-date are skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview affected shops without writing to the database.",
        )
        parser.add_argument(
            "--shop_id",
            type=int,
            default=None,
            help="Force-regenerate the public_token for this single shop, overwriting any existing token.",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Only backfill shops whose public_token is missing/empty; skip shops that already have any token.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        shop_id = options["shop_id"]
        missing_only = options["missing_only"]

        if shop_id is not None and missing_only:
            self.stderr.write(
                self.style.ERROR("--shop_id and --missing-only cannot be used together.")
            )
            return

        if shop_id is not None:
            shops = Shop.objects.filter(deleted__isnull=True, id=shop_id)
            if not shops.exists():
                self.stderr.write(
                    self.style.ERROR(f"No non-deleted shop found with id={shop_id}.")
                )
                return
            mode_label = f"shop_id={shop_id}, forced"
        else:
            shops = Shop.objects.filter(deleted__isnull=True)
            mode_label = "missing-only" if missing_only else None

        shops = shops.order_by("id")
        total = shops.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No shops found. Nothing to do."))
            return

        mode_suffix = f" ({mode_label})" if mode_label else ""
        self.stdout.write(f"{'[DRY RUN] ' if dry_run else ''}Processing {total} shop(s){mode_suffix}...")

        updated = 0
        skipped = 0

        for shop in shops:
            needs_slug = not shop.slug

            if shop_id is not None:
                needs_token = True
            elif missing_only:
                needs_token = not shop.public_token
            else:
                # Regenerate token if absent OR not exactly 12 chars (old 10-char format)
                needs_token = not shop.public_token or len(shop.public_token) != 12

            if not needs_slug and not needs_token:
                skipped += 1
                continue

            # Describe what will change (for logging)
            reasons = []
            if needs_slug:
                reasons.append("slug missing")
            if needs_token:
                if shop_id is not None:
                    reasons.append("forced regeneration")
                elif not shop.public_token:
                    reasons.append("token missing")
                else:
                    reasons.append(f"token outdated ({len(shop.public_token)}-char → 12-char)")

            if dry_run:
                self.stdout.write(
                    f"  [would update] shop id={shop.id} name='{shop.name}' — {', '.join(reasons)}"
                )
                updated += 1
                continue

            if needs_slug:
                shop.slug = slugify(shop.name)
            if needs_token:
                shop.public_token = generate_unique_public_token()

            shop.save(update_fields=["slug", "public_token"])
            updated += 1
            self.stdout.write(
                f"  Updated shop id={shop.id} name='{shop.name}' "
                f"→ slug='{shop.slug}' token='{shop.public_token}' ({', '.join(reasons)})"
            )

        suffix = " (dry run — no changes written)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {updated}/{total} shop(s) updated, {skipped} already up-to-date.{suffix}"
            )
        )
