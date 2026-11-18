"""
Management command: backfill_shop_s3_folder_name
=================================================

Purpose
-------
Maps every existing Shop to the top-level S3 folder it ACTUALLY uses today,
storing it in `Shop.s3_folder_name`. Every shop-scoped upload (product
images, logos, booking thumbnails/documents, tailor images, bug reports)
reads that stored folder via `Shop.s3_folder`, so a shop rename no longer
starts a new folder.

New shops get `s3_folder_name` automatically on creation (see `Shop.save()`
and `shop.utils.s3_folder.allocate_s3_folder_name`), so this command exists
to backfill shops created before the field existed.

This command NEVER creates, renames, moves or deletes S3 objects. It only
reads the bucket's top-level folder listing and writes `s3_folder_name`.

Modes / arguments
-----------------
  --missing-only   Process only shops with no s3_folder_name (recommended).
  (no flag)        Process ALL shops. Shops that already have a value are
                   VERIFIED, not overwritten: reported [OK] or [MISMATCH].
  --overwrite      With all-shops mode, actually replace [MISMATCH] values
                   (and allow --set to replace an existing value).
  --shop_ids       Comma-separated shop IDs to scope either mode, e.g. 12,45.
  --set            Manually pin folders, e.g. --set 57=Zara_Fashions,60=Old Store
                   (used to resolve [REVIEW] shops). Applied before evidence.
  --skip-s3        Do not list the bucket. Shops with no DB evidence are then
                   [DEFERRED] (left unmapped) instead of assigned.
  --dry-run        Preview only, no writes.
  --force          Skip the confirmation prompt.
  --no-input       Never prompt to choose a folder (see "Interactive
                   resolution" below); unresolved shops are skipped.

Usage
-----
    python manage.py backfill_shop_s3_folder_name --missing-only --dry-run
    python manage.py backfill_shop_s3_folder_name --missing-only
    python manage.py backfill_shop_s3_folder_name --missing-only --set 57=Zara_Fashions
    python manage.py backfill_shop_s3_folder_name --shop_ids 42 --dry-run
    python manage.py backfill_shop_s3_folder_name --dry-run              # audit all shops
    python manage.py backfill_shop_s3_folder_name --overwrite --shop_ids 42

How the folder is decided (first matching rule wins)
----------------------------------------------------
  1. --set value                                         -> [MANUAL]
  2. Exactly one distinct "<folder>/product_images/..." folder across the
     shop's product image/thumbnail URLs (incl. soft-deleted products,
     whose objects still exist)                          -> [ASSIGN]
  3. Two or more such folders (shop renamed before this fix, or products
     copied from another shop keep that shop's image URLs)
                                                         -> [REVIEW] skipped,
     candidates printed with counts; resolve with --shop_ids <id>
     (interactive) or --set.
  4. No product images, but Shop.img is "<folder>/shop_images/..."
                                                         -> [ASSIGN]
  5. No DB evidence: name variants used by past/present upload code
     (raw name, spaces->"_", legacy regex, new standard) that exist as
     top-level S3 folders: exactly one -> [ASSIGN], several -> [REVIEW]
  6. Nothing exists anywhere -> [NEW] standard name ("BB Shop" -> "BB_Shop"),
     suffixed (_<place>, _2, ...) if already taken by another shop, by an
     S3 folder, or by an earlier assignment in this run.

Interactive resolution (single shop)
------------------------------------
Bulk runs skip [REVIEW] / [DEFERRED] shops. When the command is run for ONE
shop (--shop_ids <id>) and that shop cannot be resolved automatically, it
lists numbered options instead and saves the one you pick:

    $ python manage.py backfill_shop_s3_folder_name --shop_ids 7
      Choose the S3 folder for shop 7 'BB Shop':
        1) BB_shop_1         2164 product images (earliest) | in S3
        2) BB_Shop           117 product images | fallback from current name | in S3 | matches current name
        3) Bookie_buddy_Dev  41 product images | in S3 | ALREADY MAPPED to shop 48 'Fashion Gems'
        s) skip (leave unchanged)
        f) filter all S3 folders by text
      Enter option [1-3, s, f] (empty = skip): 2

  - Existing folders come first (current value, product-image folders by
    image count, logo folder, S3 folders matching the name); the LAST option
    is the fallback built from the current shop name with the standard
    convention ("BB Shop" -> "BB_Shop", suffixed if another shop owns it).
    If the fallback is already listed, it is marked on that line instead.
  - "f" searches every top-level S3 folder (from the listing already loaded)
    for a folder the evidence did not find.
  - Picking a folder another shop is mapped to asks for an extra yes/no.
  - Empty input, "s", Ctrl+C, closed stdin or 3 invalid entries skip the
    shop with nothing saved. The choice still goes through the normal final
    confirmation (unless --force) and concurrency-safe write.
  - An already-mapped shop is only offered a choice with --overwrite.
  - No prompt with --no-input, --dry-run (options are printed only), or when
    stdin is not a terminal (CI / cron), so automated runs never hang.

Existing folders are kept EXACTLY as they are in S3 (e.g. a legacy
"Old Store" folder with a space is preserved, not renamed to "Old_Store").

Duplicate guard: if an evidence-based folder (rules 1-5) is already mapped to
another shop, both shops genuinely use it today - it is assigned so no shop
loses access, and reported as [SHARED with shop N]. [NEW] folders are never
shared.

How it works (bulk, no N+1 queries)
-----------------------------------
  - ONE query for candidate shops, ONE for all existing mappings, ONE
    streamed query for product image URLs, ONE paginated S3
    list_objects_v2(Delimiter="/") call set (top-level folder names only,
    not every object).
  - Decisions are made in memory.
  - Writes: inside one transaction.atomic(), ONE query re-reads the current
    s3_folder_name of every shop about to be written (so a value set
    concurrently - e.g. by a shop creation - is never overwritten), then ONE
    batched bulk_update.

Safety / idempotency
--------------------
  - --missing-only never touches a shop that already has a value.
  - All-shops mode only reports mismatches unless --overwrite is given.
  - [REVIEW] / [DEFERRED] shops stay unmapped, which keeps today's
    behaviour (Shop.s3_folder falls back to the legacy name-derived folder).
  - --shop_ids and --set are validated before anything else; any invalid
    input aborts with no writes.
  - If the S3 listing fails the command aborts (use --skip-s3 to proceed
    with DB evidence only).
  - Re-running is safe; a second --missing-only run finds nothing to do
    except shops still awaiting review.

When to run
-----------
  After the migration adding Shop.s3_folder_name is applied, and BEFORE
  relying on stored folders in production:
    1. --missing-only --dry-run, review [REVIEW] / [SHARED] rows
    2. --missing-only (with --set for reviewed shops)
    3. --dry-run (all shops) as a [MISMATCH] audit
"""
import logging
import sys
from collections import Counter, defaultdict
from urllib.parse import unquote, urlparse

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from services.models import Product
from shop.models import Shop
from shop.utils.s3_folder import (
    build_s3_folder_name,
    candidate_s3_folder_names,
    legacy_s3_segment,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
PRODUCT_CHUNK_SIZE = 5000
MAX_IN_FILTER = 1000

ASSIGN = "assign"
MANUAL = "manual"
NEW = "new"
REVIEW = "review"
DEFERRED = "deferred"
OK = "ok"
MISMATCH = "mismatch"
SELECTED = "selected"

MAX_INVALID_ATTEMPTS = 3
MAX_FILTER_RESULTS = 20


class Command(BaseCommand):
    help = (
        "Map each Shop to the S3 folder it actually uses (Shop.s3_folder_name) so "
        "renames no longer create new folders. Never moves S3 objects. Use "
        "--missing-only for the safe first run; --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes.")
        parser.add_argument(
            "--shop_ids",
            type=str,
            default=None,
            help="Comma-separated list of shop IDs to scope to, e.g. 12,45,78.",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Only process shops without an s3_folder_name.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Allow replacing an existing s3_folder_name ([MISMATCH] rows / --set).",
        )
        parser.add_argument(
            "--set",
            dest="manual",
            type=str,
            default=None,
            help='Manual mappings "shop_id=folder,..." e.g. 57=Zara_Fashions',
        )
        parser.add_argument(
            "--skip-s3",
            action="store_true",
            help="Do not list the S3 bucket; shops without DB evidence are deferred.",
        )
        parser.add_argument("--force", action="store_true", help="Skip confirmation prompt.")
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Never prompt to choose a folder for an unresolved single shop (it is skipped).",
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        missing_only = options["missing_only"]
        overwrite = options["overwrite"]
        skip_s3 = options["skip_s3"]
        force = options["force"]
        no_input = options["no_input"]

        if missing_only and overwrite:
            raise CommandError("--overwrite cannot be combined with --missing-only.")

        shop_ids = self._parse_shop_ids(options["shop_ids"])
        manual = self._parse_manual(options["manual"])

        all_ids_to_check = set(shop_ids or []) | set(manual)
        if all_ids_to_check:
            found = set(
                Shop.objects.filter(deleted__isnull=True, id__in=all_ids_to_check)
                .values_list("id", flat=True)
            )
            missing = sorted(all_ids_to_check - found)
            if missing:
                raise CommandError(
                    f"No non-deleted shop found for id(s): {', '.join(map(str, missing))}."
                )
        if shop_ids is not None and set(manual) - set(shop_ids):
            raise CommandError("--set contains shop IDs outside --shop_ids.")

        # --- Candidate shops (1 query) ---
        qs = Shop.objects.filter(deleted__isnull=True).only(
            "id", "name", "place", "img", "s3_folder_name"
        )
        if shop_ids is not None:
            qs = qs.filter(id__in=shop_ids)
        if missing_only:
            qs = qs.filter(Q(s3_folder_name__isnull=True) | Q(s3_folder_name=""))
        shops = list(qs.order_by("id"))

        # Interactive resolution is only offered for a single-shop run, never in bulk.
        single_shop = shop_ids is not None and len(shops) == 1
        can_prompt = single_shop and not no_input and not dry_run and sys.stdin.isatty()

        mode_label = "missing-only" if missing_only else ("all shops, overwrite" if overwrite else "all shops, verify")
        scope_label = f"shop_ids={shop_ids}" if shop_ids is not None else "all shops"
        interactive_label = " | interactive" if can_prompt else ""
        self.stdout.write(f"Scope: {scope_label} | mode: {mode_label}{interactive_label}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN enabled. No DB writes."))

        if not shops:
            self.stdout.write(self.style.SUCCESS("No matching shops found. Nothing to do."))
            return

        # --- Existing mappings, incl. soft-deleted shops (1 query) ---
        owners = defaultdict(set)
        for sid, folder in (
            Shop.all_objects.exclude(s3_folder_name__isnull=True)
            .exclude(s3_folder_name="")
            .values_list("id", "s3_folder_name")
        ):
            owners[folder].add(sid)

        # --- S3 top-level folders (1 paginated listing) ---
        s3_folders = None if skip_s3 else self._list_top_level_folders()
        if s3_folders is not None:
            self.stdout.write(f"S3 top-level folders found: {len(s3_folders)}")
        else:
            self.stdout.write(self.style.WARNING("S3 listing skipped (--skip-s3)."))

        # --- Product image evidence (1 streamed query) ---
        product_evidence = self._load_product_evidence({s.id for s in shops})

        # --- Decide ---
        reserved = set(owners) | (s3_folders or set())
        writes = []  # (shop, expected_current_value, new_value)
        counts = Counter()

        for shop in shops:
            current = shop.s3_folder_name or None
            action, folder, note = self._decide(
                shop, manual, product_evidence.get(shop.id), s3_folders, reserved
            )

            # Single-shop run that could not be resolved automatically:
            # let the operator pick from numbered options.
            if action in (REVIEW, DEFERRED) and single_shop and not no_input and (not current or overwrite):
                self.stdout.write(self.style.WARNING(
                    f"  [{action.upper()}]{' ' * (9 - len(action))}shop id={shop.id} '{shop.name}' "
                    f"(place: {shop.place or '-'}) - could not pick a folder automatically ({note})."
                ))
                if dry_run:
                    prompt_off_reason = "DRY RUN - would prompt here"
                elif not can_prompt:
                    prompt_off_reason = "stdin is not interactive - not prompting"
                else:
                    prompt_off_reason = None
                picked = self._resolve_interactively(
                    shop, current, product_evidence.get(shop.id), s3_folders, owners, prompt_off_reason
                )
                if picked and picked != current:
                    action, folder, note = SELECTED, picked, "selected interactively"
                elif picked:
                    counts[OK] += 1
                    self.stdout.write(f"  [OK]       shop id={shop.id} '{shop.name}' = '{current}' (kept)")
                    continue
                else:
                    counts[action] += 1
                    self.stdout.write(self.style.WARNING(
                        f"  [{action.upper()}]{' ' * (9 - len(action))}shop id={shop.id} '{shop.name}' -> skipped"
                    ))
                    continue

            if action == SELECTED:
                counts[SELECTED] += 1
                shared_with = sorted(owners.get(folder, set()) - {shop.id})
                if shared_with:
                    counts["shared"] += 1
                    note += f", SHARED with shop {', '.join(map(str, shared_with))}"
                change = f"'{current}' -> '{folder}'" if current else f"-> '{folder}'"
                self.stdout.write(self.style.SUCCESS(
                    f"  [SELECTED] shop id={shop.id} '{shop.name}' {change} ({note})"
                ))
                writes.append((shop, current, folder))
                owners[folder].add(shop.id)
                reserved.add(folder)
                continue

            if current:
                # Already mapped: verify against evidence, never overwrite silently.
                if action == REVIEW:
                    counts[REVIEW] += 1
                    hint = " (kept; add --overwrite to choose a different folder)" if single_shop else ""
                    self.stdout.write(self.style.WARNING(
                        f"  [REVIEW]   shop id={shop.id} '{shop.name}' stored='{current}', {note}{hint}"
                    ))
                    continue
                if action not in (ASSIGN, MANUAL) or folder == current:
                    # Evidence agrees, or there is none to contradict the stored value.
                    counts[OK] += 1
                    self.stdout.write(f"  [OK]       shop id={shop.id} '{shop.name}' = '{current}'")
                    continue

                counts[MISMATCH] += 1
                if overwrite:
                    writes.append((shop, current, folder))
                    self.stdout.write(self.style.WARNING(
                        f"  [MISMATCH] shop id={shop.id} '{shop.name}' stored='{current}' -> '{folder}' "
                        f"(will overwrite; {note})"
                    ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f"  [MISMATCH] shop id={shop.id} '{shop.name}' stored='{current}' evidence='{folder}' "
                        f"({note}) - not changed, use --overwrite"
                    ))
                continue

            counts[action] += 1
            if action in (REVIEW, DEFERRED):
                hint = "" if single_shop else f" -> run with --shop_ids {shop.id} to choose interactively"
                self.stdout.write(self.style.WARNING(
                    f"  [{action.upper()}]{' ' * (9 - len(action))}shop id={shop.id} '{shop.name}' -> skipped, {note}{hint}"
                ))
                continue

            shared_with = sorted(owners.get(folder, set()) - {shop.id})
            if shared_with:
                counts["shared"] += 1
                note += f", SHARED with shop {', '.join(map(str, shared_with))}"
            self.stdout.write(
                f"  [{action.upper()}]{' ' * (9 - len(action))}shop id={shop.id} '{shop.name}' -> '{folder}' ({note})"
            )
            writes.append((shop, None, folder))
            owners[folder].add(shop.id)
            reserved.add(folder)

        self._print_summary(counts, len(writes), dry_run)

        if dry_run or not writes:
            return

        if not force:
            confirm = input("Proceed with s3_folder_name backfill? (yes/no): ").strip().lower()
            if confirm not in ("yes", "y"):
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        updated, skipped = self._write(writes)
        msg = f"Done. {updated} shop(s) updated, {skipped} skipped (changed concurrently)."
        self.stdout.write(self.style.SUCCESS(msg))
        logger.info("backfill_shop_s3_folder_name: %s counts=%s", msg, dict(counts))

    # ------------------------------------------------------------------
    # Argument parsing
    # ------------------------------------------------------------------

    def _parse_shop_ids(self, raw):
        if not raw:
            return None
        try:
            ids = [int(part.strip()) for part in raw.split(",") if part.strip()]
        except ValueError:
            raise CommandError(f"--shop_ids must be a comma-separated list of integers, got: {raw!r}")
        if not ids:
            raise CommandError("--shop_ids must contain at least one shop ID.")
        return ids

    def _parse_manual(self, raw):
        manual = {}
        if not raw:
            return manual
        for part in raw.split(","):
            if not part.strip():
                continue
            sid, sep, folder = part.partition("=")
            try:
                sid = int(sid.strip())
            except ValueError:
                raise CommandError(f"--set entry {part!r} must look like <shop_id>=<folder>.")
            if not sep or not folder or folder != folder.strip() or "/" in folder:
                raise CommandError(
                    f"--set entry {part!r}: folder must be non-empty, without '/' or surrounding spaces."
                )
            manual[sid] = folder
        return manual

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def _list_top_level_folders(self):
        from services.s3_service import S3Service

        bucket = settings.AWS_STORAGE_BUCKET_NAME
        if not bucket:
            raise CommandError("AWS_STORAGE_BUCKET_NAME is not configured (or use --skip-s3).")
        folders = set()
        try:
            paginator = S3Service.get_client().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Delimiter="/"):
                for prefix in page.get("CommonPrefixes", []):
                    folders.add(prefix["Prefix"].rstrip("/"))
        except (ClientError, BotoCoreError) as exc:
            logger.error("backfill_shop_s3_folder_name: S3 listing failed: %s", exc)
            raise CommandError(
                f"Could not list S3 bucket '{bucket}': {exc}. Re-run with --skip-s3 to use DB evidence only."
            )
        return folders

    @staticmethod
    def _key_from_url(url):
        """S3 object key from a stored URL (virtual-hosted or path-style)."""
        if not url:
            return None
        path = unquote(urlparse(url).path).lstrip("/")
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        if bucket and path.startswith(f"{bucket}/"):
            path = path[len(bucket) + 1:]
        return path or None

    @classmethod
    def _folder_from_url(cls, url, subfolder):
        key = cls._key_from_url(url)
        if not key:
            return None
        parts = key.split("/")
        if len(parts) >= 3 and parts[1] == subfolder and parts[0]:
            return parts[0]
        return None

    def _load_product_evidence(self, shop_ids):
        """{shop_id: {"counts": Counter(folder), "earliest": folder}} in one streamed query."""
        rows = Product.all_objects.filter(
            Q(image__isnull=False) | Q(thumbnail_image__isnull=False)
        )
        if len(shop_ids) <= MAX_IN_FILTER:
            rows = rows.filter(shop_service__shop_id__in=shop_ids)
        rows = rows.values_list("shop_service__shop_id", "image", "thumbnail_image").order_by("id")

        evidence = {}
        for sid, image, thumb in rows.iterator(chunk_size=PRODUCT_CHUNK_SIZE):
            if sid not in shop_ids:
                continue
            for url in (image, thumb):
                folder = self._folder_from_url(url, "product_images")
                if not folder:
                    continue
                entry = evidence.setdefault(sid, {"counts": Counter(), "earliest": folder})
                entry["counts"][folder] += 1
        return evidence

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def _decide(self, shop, manual, product_evidence, s3_folders, reserved):
        """Returns (action, folder or None, note)."""
        if shop.id in manual:
            folder = manual[shop.id]
            note = "manual --set"
            if s3_folders is not None and folder not in s3_folders:
                note += ", WARNING: folder not found in S3"
            return MANUAL, folder, note

        if product_evidence:
            counts = product_evidence["counts"]
            if len(counts) == 1:
                folder, n = next(iter(counts.items()))
                return ASSIGN, folder, f"product_images x{n}"
            candidates = ", ".join(
                f"{f}={n}{' (earliest)' if f == product_evidence['earliest'] else ''}"
                for f, n in counts.most_common()
            )
            return REVIEW, None, f"multiple product folders: {candidates}"

        logo_folder = self._folder_from_url(shop.img, "shop_images")
        if logo_folder:
            return ASSIGN, logo_folder, "from shop logo URL"

        if s3_folders is None:
            return DEFERRED, None, "no DB evidence and S3 listing skipped"

        existing = sorted(v for v in self._name_variants(shop.name) if v in s3_folders)
        if len(existing) == 1:
            return ASSIGN, existing[0], "S3 folder exists, no DB images"
        if len(existing) > 1:
            return REVIEW, None, f"multiple matching S3 folders: {', '.join(existing)}"

        for candidate in candidate_s3_folder_names(shop.name, shop.place):
            if candidate not in reserved:
                return NEW, candidate, "no existing folder"
        return REVIEW, None, "could not allocate a free folder name"

    @staticmethod
    def _name_variants(name):
        """Folder names past/present upload code could have derived from `name`."""
        name = name or ""
        variants = {
            name.strip(),
            name.replace(" ", "_"),
            name.strip().replace(" ", "_"),
            legacy_s3_segment(name),
            build_s3_folder_name(name),
        }
        return {v for v in variants if v}

    # ------------------------------------------------------------------
    # Interactive resolution (single shop)
    # ------------------------------------------------------------------

    @staticmethod
    def _new_option(folder, **flags):
        entry = {
            "folder": folder, "image_count": 0, "earliest": False, "current": False,
            "logo": False, "name_match": False, "fallback": False, "search": False,
        }
        entry.update(flags)
        return entry

    def _build_options(self, shop, current, product_evidence, s3_folders, owners):
        """Numbered choices for one shop, in display order: current value,
        product-image folders (most images first), logo folder, S3 folders
        matching the name, then the current-name fallback. Duplicates merge."""
        options = {}

        def add(folder, **flags):
            if folder in options:
                options[folder].update(flags)
            else:
                options[folder] = self._new_option(folder, **flags)

        if current:
            add(current, current=True)
        if product_evidence:
            for folder, n in product_evidence["counts"].most_common():
                add(folder, image_count=n, earliest=folder == product_evidence["earliest"])
        logo_folder = self._folder_from_url(shop.img, "shop_images")
        if logo_folder:
            add(logo_folder, logo=True)
        name_variants = self._name_variants(shop.name)
        if s3_folders is not None:
            for folder in sorted(name_variants & s3_folders):
                add(folder)

        # Fallback: standard name from the CURRENT shop name ("BB Shop" -> "BB_Shop"),
        # suffixed (_<place>, _2, ...) if another shop already owns it.
        for candidate in candidate_s3_folder_names(shop.name, shop.place):
            if not owners.get(candidate, set()) - {shop.id}:
                add(candidate, fallback=True)
                break

        for entry in options.values():
            entry["name_match"] = entry["folder"] in name_variants
        return self._annotate(list(options.values()), shop, s3_folders, owners)

    @staticmethod
    def _annotate(options, shop, s3_folders, owners):
        for entry in options:
            entry["in_s3"] = None if s3_folders is None else entry["folder"] in s3_folders
            entry["owned_by"] = sorted(owners.get(entry["folder"], set()) - {shop.id})
        return options

    @staticmethod
    def _format_option(entry, owner_names):
        parts = []
        if entry["current"]:
            parts.append("current value")
        if entry["image_count"]:
            earliest = " (earliest)" if entry["earliest"] else ""
            parts.append(f"{entry['image_count']} product images{earliest}")
        if entry["logo"]:
            parts.append("shop logo folder")
        if entry["search"]:
            parts.append("filter match")
        if entry["fallback"]:
            parts.append("fallback from current name")
        if entry["in_s3"] is True:
            parts.append("in S3")
        elif entry["in_s3"] is False:
            parts.append("new folder (not in S3 yet)")
        if entry["name_match"]:
            parts.append("matches current name")
        if entry["owned_by"]:
            owned = ", ".join(f"{sid} '{owner_names.get(sid, '?')}'" for sid in entry["owned_by"])
            parts.append(f"ALREADY MAPPED to shop {owned}")
        return " | ".join(parts)

    def _print_options(self, shop, options):
        owner_ids = {sid for entry in options for sid in entry["owned_by"]}
        owner_names = (
            dict(Shop.all_objects.filter(id__in=owner_ids).values_list("id", "name"))
            if owner_ids else {}
        )
        width = max(len(entry["folder"]) for entry in options)
        self.stdout.write("")
        self.stdout.write(f"  Choose the S3 folder for shop {shop.id} '{shop.name}':")
        for i, entry in enumerate(options, start=1):
            self.stdout.write(
                f"    {i}) {entry['folder']:<{width}}  {self._format_option(entry, owner_names)}"
            )
        self.stdout.write("    s) skip (leave unchanged)")
        self.stdout.write("    f) filter all S3 folders by text")

    def _ask(self, prompt):
        """input() that turns Ctrl+C / closed stdin into None (= skip the shop)."""
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("  Input aborted - shop skipped, nothing saved."))
            return None

    def _filter_options(self, text, shop, options, s3_folders, owners):
        """Extra options: S3 top-level folders containing `text` (case-insensitive)."""
        listed = {entry["folder"] for entry in options}
        matches = sorted(f for f in s3_folders if text.lower() in f.lower() and f not in listed)
        if len(matches) > MAX_FILTER_RESULTS:
            self.stdout.write(
                f"  {len(matches)} matches, showing the first {MAX_FILTER_RESULTS} - refine the text to narrow down."
            )
        name_variants = self._name_variants(shop.name)
        new = [
            self._new_option(f, search=True, name_match=f in name_variants)
            for f in matches[:MAX_FILTER_RESULTS]
        ]
        return self._annotate(new, shop, s3_folders, owners)

    def _resolve_interactively(self, shop, current, product_evidence, s3_folders, owners, prompt_off_reason):
        """Shows numbered folder options; returns the chosen folder, or None to skip."""
        options = self._build_options(shop, current, product_evidence, s3_folders, owners)
        self._print_options(shop, options)
        if prompt_off_reason:
            self.stdout.write(self.style.WARNING(f"  ({prompt_off_reason})"))
            return None

        invalid = 0
        while invalid < MAX_INVALID_ATTEMPTS:
            answer = self._ask(f"  Enter option [1-{len(options)}, s, f] (empty = skip): ")
            if answer is None or answer.lower() in ("", "s"):
                return None
            answer = answer.lower()

            if answer == "f":
                if s3_folders is None:
                    self.stdout.write(self.style.WARNING("  S3 listing was skipped (--skip-s3); nothing to filter."))
                    continue
                text = self._ask("  Filter text: ")
                if text is None:
                    return None
                if not text:
                    continue
                extra = self._filter_options(text, shop, options, s3_folders, owners)
                if not extra:
                    self.stdout.write(self.style.WARNING(f"  No other S3 folders contain {text!r}."))
                    continue
                options += extra
                self._print_options(shop, options)
                continue

            if not answer.isdigit() or not 1 <= int(answer) <= len(options):
                invalid += 1
                self.stdout.write(self.style.WARNING(
                    f"  Invalid option {answer!r} ({invalid}/{MAX_INVALID_ATTEMPTS})."
                ))
                continue

            entry = options[int(answer) - 1]
            if entry["owned_by"]:
                confirm = self._ask(
                    f"  '{entry['folder']}' is already used by shop "
                    f"{', '.join(map(str, entry['owned_by']))}. Share it with shop {shop.id}? (yes/no): "
                )
                if confirm is None:
                    return None
                if confirm.lower() not in ("yes", "y"):
                    continue
            self.stdout.write(f"  Selected '{entry['folder']}' for shop {shop.id}.")
            return entry["folder"]

        self.stdout.write(self.style.WARNING("  Too many invalid attempts - shop skipped, nothing saved."))
        return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _write(self, writes):
        ids = [shop.id for shop, _, _ in writes]
        with transaction.atomic():
            current = dict(
                Shop.all_objects.select_for_update()
                .filter(id__in=ids)
                .values_list("id", "s3_folder_name")
            )
            to_update = []
            skipped = 0
            for shop, expected, folder in writes:
                if (current.get(shop.id) or None) != expected:
                    skipped += 1
                    self.stdout.write(self.style.WARNING(
                        f"  [SKIPPED]  shop id={shop.id}: s3_folder_name changed to "
                        f"'{current.get(shop.id)}' since it was read"
                    ))
                    continue
                shop.s3_folder_name = folder
                to_update.append(shop)
            Shop.all_objects.bulk_update(to_update, ["s3_folder_name"], batch_size=BATCH_SIZE)
        return len(to_update), skipped

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self, counts, write_count, dry_run):
        self.stdout.write("")
        self.stdout.write(f'  {"ACTION":<10} {"COUNT":>6}')
        self.stdout.write(f'  {"-" * 10} {"-" * 6}')
        for action in (ASSIGN, MANUAL, SELECTED, NEW, OK, MISMATCH, REVIEW, DEFERRED, "shared"):
            if counts.get(action):
                self.stdout.write(f"  {action:<10} {counts[action]:>6}")
        if counts.get(REVIEW):
            self.stdout.write(self.style.WARNING(
                "  review rows were skipped - resolve with --shop_ids <id> (choose interactively) "
                "or --set <shop_id>=<folder>"
            ))
        verb = "[DRY RUN] would set" if dry_run else "Will set"
        self.stdout.write(self.style.SUCCESS(f"{verb} s3_folder_name on {write_count} shop(s)."))
