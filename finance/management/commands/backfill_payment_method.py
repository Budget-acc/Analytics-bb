"""
Payment Method Backfill Command
================================

PURPOSE
-------
Backfills the `payment_method` field on every payment-related table, derived from the
`account_type` of the `Account` that each row is linked to:

    account_type == 'cash'  ->  payment_method = 'cash'
    anything else (e.g. 'bank')  ->  payment_method = 'upi'

DYNAMIC MODEL DISCOVERY
------------------------
Rather than hardcoding a fixed list of tables, this command inspects Django's model registry
(`django.apps.apps.get_models()`) at runtime and targets EVERY model, in EVERY installed app,
that has BOTH:
  1. A ForeignKey (or OneToOneField) to `finance.models.Account`
  2. A `payment_method` field

This means newly added tables that reference Account and carry a payment_method field are
picked up automatically on the next run, with no code changes required. Models that store a
differently-named field (e.g. `refund_method` on RefundDetails / TailorOrderRefund) are
intentionally NOT touched by this command — it only targets `payment_method`.

Run with --dry-run to see the exact list of discovered models before writing anything.

MAPPING LOGIC
-------------
  Account.account_type == 'cash'  ->  payment_method = 'cash'
  Account.account_type == 'bank'  ->  payment_method = 'upi'
  (any other/future account_type is treated the same as 'bank' -> 'upi', per spec: "default to UPI")

ROW ELIGIBILITY
----------------
A row is only updated when its linked account is USABLE:
  - account is not null
  - account.deleted is null (not soft-deleted)
  - account.is_active is True

If the target model is itself soft-deleted (has a `deleted` field, e.g. SafeDeleteModel),
soft-deleted rows (deleted is not null) are skipped as well.

ARGUMENTS
---------
  --shop-id        Optional. Restrict to a single shop, filtered via account__shop_id
                    (Account always has a shop FK, so this works uniformly across every
                    target model regardless of how that model itself relates to shop).
                    Omit to process all shops.
  --missing-only    Optional flag. When set, only rows where payment_method is currently
                    null or empty string are updated. Omit to overwrite payment_method on
                    every eligible row regardless of its current value.
  --dry-run         Preview: count rows that would be updated, without writing anything.
  --force           Skip the interactive confirmation prompt (needed for automated runs).

USAGE
-----
  python manage.py backfill_payment_method --dry-run
  python manage.py backfill_payment_method --shop-id 115 --dry-run
  python manage.py backfill_payment_method --missing-only --dry-run
  python manage.py backfill_payment_method --shop-id 115 --force
  python manage.py backfill_payment_method --missing-only --force
  python manage.py backfill_payment_method --force

N+1 QUERY PREVENTION / PERFORMANCE
------------------------------------
No Python-side row loop is used. For each discovered model, the command issues exactly TWO
bulk `UPDATE ... WHERE` statements (one for cash accounts, one for everything else), built by
filtering across the FK relation directly in SQL (`<fk_field>__account_type=...`). This keeps
the cost at O(2 UPDATE statements per model) regardless of how many rows match, with no objects
loaded into memory and no per-row queries.

SAFEGUARDS
----------
  --dry-run   Counts what would be updated without writing any data.
  --force     Skips the confirmation prompt (required for automated pipelines).
  --shop-id   Restricts to a single shop; omit to process all shops.
"""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models as dj_models, transaction
from django.db.models import Q

from finance.models import Account

CASH = "cash"
UPI = "upi"


class Command(BaseCommand):
    help = (
        "Backfill payment_method on every model with an Account FK, derived from the "
        "linked account's account_type (cash -> cash, everything else -> upi)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--shop-id", type=int, default=None, help="Restrict to one shop")
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Only update rows where payment_method is null/empty",
        )
        parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
        parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        shop_id = options["shop_id"]
        missing_only = options["missing_only"]
        dry_run = options["dry_run"]
        force = options["force"]

        targets = self._discover_target_models()

        if not targets:
            self.stdout.write(self.style.ERROR("No models found with an Account FK + payment_method field."))
            return

        self.stdout.write(f"Discovered {len(targets)} target model(s):")
        for t in targets:
            model = t["model"]
            self.stdout.write(f"  - {model._meta.app_label}.{model.__name__} (fk={t['fk_field']})")

        if shop_id:
            self.stdout.write(f"Scope: shop_id={shop_id}")
        else:
            self.stdout.write("Scope: all shops")
        self.stdout.write(f"Missing-only: {missing_only}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN enabled. No DB writes."))

        if not dry_run and not force:
            confirm = input("Proceed with payment_method backfill? (yes/no): ").strip().lower()
            if confirm not in ("yes", "y"):
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        results = []
        for t in targets:
            cash_count, upi_count = self._process_model(t, shop_id, missing_only, dry_run)
            results.append((t["model"], cash_count, upi_count))

        self._print_summary(results, dry_run)

    # ------------------------------------------------------------------
    # Dynamic discovery
    # ------------------------------------------------------------------

    def _discover_target_models(self):
        """Scan the full Django model registry for models with an Account FK + payment_method."""
        targets = []
        for model in apps.get_models():
            fk_field = None
            has_payment_method = False
            has_soft_delete = False

            for f in model._meta.get_fields():
                if (
                    fk_field is None
                    and isinstance(f, (dj_models.ForeignKey, dj_models.OneToOneField))
                    and f.related_model is Account
                ):
                    fk_field = f.name
                if getattr(f, "name", None) == "payment_method":
                    has_payment_method = True
                if getattr(f, "name", None) == "deleted":
                    has_soft_delete = True

            if fk_field is None or not has_payment_method:
                continue

            targets.append({
                "model": model,
                "fk_field": fk_field,
                "has_soft_delete": has_soft_delete,
            })

        targets.sort(key=lambda t: (t["model"]._meta.app_label, t["model"].__name__))
        return targets

    # ------------------------------------------------------------------
    # Per-model processing
    # ------------------------------------------------------------------

    def _process_model(self, target, shop_id, missing_only, dry_run):
        model = target["model"]
        fk_field = target["fk_field"]
        has_soft_delete = target["has_soft_delete"]

        filters = {
            f"{fk_field}__isnull": False,
            f"{fk_field}__deleted__isnull": True,
            f"{fk_field}__is_active": True,
        }
        if shop_id:
            filters[f"{fk_field}__shop_id"] = shop_id
        if has_soft_delete:
            filters["deleted__isnull"] = True

        qs = model.objects.filter(**filters)

        if missing_only:
            qs = qs.filter(Q(payment_method__isnull=True) | Q(payment_method=""))

        cash_qs = qs.filter(**{f"{fk_field}__account_type": "cash"})
        other_qs = qs.filter(~Q(**{f"{fk_field}__account_type": "cash"}))

        if dry_run:
            return cash_qs.count(), other_qs.count()

        with transaction.atomic():
            cash_updated = cash_qs.update(payment_method=CASH)
            other_updated = other_qs.update(payment_method=UPI)

        return cash_updated, other_updated

    # ------------------------------------------------------------------
    # Summary report
    # ------------------------------------------------------------------

    def _print_summary(self, results, dry_run):
        verb = "Would update" if dry_run else "Updated"
        self.stdout.write("")
        self.stdout.write(f'  {"MODEL":<55} {"CASH":>8} {"UPI":>8} {"TOTAL":>8}')
        self.stdout.write(f'  {"-"*55} {"-"*8} {"-"*8} {"-"*8}')

        grand_cash = 0
        grand_upi = 0
        for model, cash_count, upi_count in results:
            label = f"{model._meta.app_label}.{model.__name__}"
            total = cash_count + upi_count
            grand_cash += cash_count
            grand_upi += upi_count
            self.stdout.write(f"  {label:<55} {cash_count:>8} {upi_count:>8} {total:>8}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{verb} {grand_cash + grand_upi} row(s) total"))
        self.stdout.write(f"  cash: {grand_cash}")
        self.stdout.write(f"  upi:  {grand_upi}")
