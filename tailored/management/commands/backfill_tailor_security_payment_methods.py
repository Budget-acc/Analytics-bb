from django.core.management.base import BaseCommand
from django.db import transaction

from shop.models import Shop
from tailored.models import TailorOrderSecurityPayment


class Command(BaseCommand):
    help = (
        "Backfill TailorOrderSecurityPayment.payment_method from the linked "
        "account type (cash -> cash, bank -> upi)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--shop-id",
            type=int,
            default=None,
            help="Run for one shop only",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview the rows that would be updated",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run without confirmation",
        )

    def handle(self, *args, **options):
        shop_id = options["shop_id"]
        dry_run = options["dry_run"]
        force = options["force"]

        if shop_id and not Shop.objects.filter(id=shop_id).exists():
            self.stdout.write(self.style.ERROR("No shops found for given input."))
            return

        shops_count = Shop.objects.filter(id=shop_id).count() if shop_id else Shop.objects.count()
        self.stdout.write(f"Shops to process: {shops_count}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN enabled. No DB writes."))

        if not dry_run and not force:
            confirm = input(
                "Proceed with tailor security payment method backfill? (yes/no): "
            ).strip().lower()
            if confirm != "yes":
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        total_updated = self._process(shop_id=shop_id, dry_run=dry_run)

        self.stdout.write(self.style.SUCCESS("Backfill completed"))
        summary_label = "Total rows to update" if dry_run else "Total rows updated"
        self.stdout.write(f"{summary_label}: {total_updated}")

    def _process(self, shop_id=None, dry_run=False):
        cash_queryset = self._cash_queryset(shop_id=shop_id)
        bank_queryset = self._bank_queryset(shop_id=shop_id)

        cash_count = cash_queryset.count()
        bank_count = bank_queryset.count()
        total_count = cash_count + bank_count

        self.stdout.write(
            f"Cash-linked rows: {cash_count}, bank-linked rows: {bank_count}"
        )

        if dry_run or total_count == 0:
            return total_count

        with transaction.atomic():
            cash_queryset.update(payment_method="cash")
            bank_queryset.update(payment_method="upi")

        return total_count

    def _base_queryset(self, shop_id=None):
        queryset = TailorOrderSecurityPayment.objects.filter(account__isnull=False)
        if shop_id:
            queryset = queryset.filter(order__shop_id=shop_id)
        return queryset

    def _cash_queryset(self, shop_id=None):
        return self._base_queryset(shop_id=shop_id).filter(
            account__account_type="cash"
        ).exclude(payment_method="cash")

    def _bank_queryset(self, shop_id=None):
        return self._base_queryset(shop_id=shop_id).filter(
            account__account_type="bank"
        ).exclude(payment_method="upi")