"""
Management command: create_shop_categories

Creates a standard set of ShopCategory records for every shop.

Usage:
    python manage.py create_shop_categories
    python manage.py create_shop_categories --dry-run
"""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from expenses_v2.api.v3.views.helpers.category_defaults import CATEGORY_DEFINITIONS
from expenses_v2.models import ShopCategory


class Command(BaseCommand):
    help = "Creates required ShopCategory records for every shop, skipping existing exact-name categories."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without persisting changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        Shop = apps.get_model("shop", "Shop")

        shops = list(Shop.objects.all())
        if not shops:
            self.stdout.write(self.style.WARNING("No shops found. Nothing to create."))
            return

        shop_ids = [shop.pk for shop in shops]
        existing_categories = ShopCategory.objects.filter(shop_id__in=shop_ids).values_list(
            "shop_id", "name"
        )
        existing_names_by_shop = {}
        for shop_id, name in existing_categories:
            existing_names_by_shop.setdefault(shop_id, set()).add(name)

        total_created = 0
        total_skipped = 0
        total_warnings = 0

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved.\n"))

        for shop in shops:
            self.stdout.write(f"Processing shop: {shop} (id={shop.pk})")
            existing_names = existing_names_by_shop.get(shop.pk, set())

            for category_type, names in CATEGORY_DEFINITIONS.items():
                for name in names:
                    if name in existing_names:
                        total_skipped += 1
                        continue

                    if dry_run:
                        self.stdout.write(f"  [dry-run] Would create category: {name} ({category_type})")
                        total_created += 1
                        continue

                    try:
                        with transaction.atomic():
                            ShopCategory.objects.create(
                                shop=shop,
                                name=name,
                                type=category_type,
                            )
                    except IntegrityError:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Skipped category because a case-insensitive duplicate already exists: {name}"
                            )
                        )
                        total_warnings += 1
                        continue

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  Created category: {name} ({category_type})"
                        )
                    )
                    total_created += 1

        self.stdout.write(self.style.SUCCESS("\nCategory creation complete."))
        self.stdout.write(f"  Categories created : {total_created}")
        self.stdout.write(f"  Categories skipped : {total_skipped}")
        if total_warnings:
            self.stdout.write(f"  Warnings          : {total_warnings} (case-insensitive duplicates)")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes were saved."))
