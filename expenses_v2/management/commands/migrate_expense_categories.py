"""
Management command: migrate_expense_categories

Reads the `type` field from every Expenses and ProductVariantExpense record,
creates a ShopCategory per shop/name (case-insensitive, deduped), and assigns
the FK back to each record.

Usage:
    python manage.py migrate_expense_categories
    python manage.py migrate_expense_categories --batch-size 500
    python manage.py migrate_expense_categories --dry-run
"""

from typing import Optional

from django.core.management.base import BaseCommand
from django.db import transaction

from expenses_v2.models import Expenses, ProductVariantExpense, ShopCategory

BATCH_SIZE_DEFAULT = 1000


class Command(BaseCommand):
    help = "Migrates existing expense type strings into ShopCategory records and assigns the FK."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE_DEFAULT,
            help=f"Number of records to process per batch (default: {BATCH_SIZE_DEFAULT}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the migration without writing any changes to the database.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved.\n"))

        # In-memory cache: (shop_id, name_lower) → ShopCategory instance.
        # Avoids repeated DB lookups for the same category within a run.
        category_cache: dict[tuple, ShopCategory] = {}

        def resolve_category(shop_id: int, type_value: str) -> Optional[ShopCategory]:
            """
            Return the ShopCategory for this shop+name, creating it if absent.
            Lookup and creation are both case-insensitive.
            """
            key = (shop_id, type_value.strip().lower())
            if key in category_cache:
                return category_cache[key]

            name_stripped = type_value.strip()
            # Prefer an existing category (case-insensitive match).
            existing = ShopCategory.objects.filter(
                shop_id=shop_id, name__iexact=name_stripped
            ).first()

            if existing:
                category_cache[key] = existing
                return existing

            if not dry_run:
                category = ShopCategory.objects.create(
                    shop_id=shop_id, name=name_stripped, type='expense'
                )
                category_cache[key] = category
                return category

            # In dry-run mode, return a placeholder so counts are correct.
            category_cache[key] = None
            return None

        expenses_updated = self._migrate_model(
            model=Expenses,
            model_label="Expenses",
            batch_size=batch_size,
            dry_run=dry_run,
            resolve_category=resolve_category,
        )

        variant_expenses_updated = self._migrate_model(
            model=ProductVariantExpense,
            model_label="ProductVariantExpense",
            batch_size=batch_size,
            dry_run=dry_run,
            resolve_category=resolve_category,
        )

        categories_created = sum(1 for v in category_cache.values() if v is not None)

        self.stdout.write(self.style.SUCCESS(
            f"\nMigration complete."
            f"\n  ShopCategory records created : {categories_created}"
            f"\n  Expenses updated             : {expenses_updated}"
            f"\n  ProductVariantExpenses updated: {variant_expenses_updated}"
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes were saved."))

    def _migrate_model(self, model, model_label, batch_size, dry_run, resolve_category):
        """
        Iterates through all records of `model` that have a non-blank `type`
        and no `category` FK yet, resolves/creates the ShopCategory, then
        bulk-updates the `category_id` column in batches.

        Returns the total number of records updated.
        """
        total_updated = 0

        qs = (
            model.objects.all_with_deleted()  # include soft-deleted records
            .filter(type__isnull=False, category__isnull=True)
            .exclude(type="")
            .only("id", "shop_id", "type", "category_id")
        )

        total = qs.count()
        self.stdout.write(
            f"{model_label}: {total} records to process."
        )

        batch: list = []

        for record in qs.iterator(chunk_size=batch_size):
            category = resolve_category(record.shop_id, record.type)
            if category is None:
                continue  # dry-run placeholder, skip
            record.category_id = category.pk
            batch.append(record)

            if len(batch) >= batch_size:
                total_updated += self._flush_batch(batch, dry_run, model_label)
                batch = []

        # Flush remaining records.
        if batch:
            total_updated += self._flush_batch(batch, dry_run, model_label)

        return total_updated

    def _flush_batch(self, batch: list, dry_run: bool, label: str) -> int:
        if dry_run:
            self.stdout.write(f"  [dry-run] Would update {len(batch)} {label} records.")
            return len(batch)

        with transaction.atomic():
            updated = type(batch[0]).objects.bulk_update(batch, ["category_id"])

        self.stdout.write(f"  Updated {updated} {label} records.")
        return updated
