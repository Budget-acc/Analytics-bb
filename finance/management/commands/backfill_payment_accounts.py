from django.core.management.base import BaseCommand
from django.db import transaction

from shop.models import Shop
from finance.models import Account

from booking_v2.models import (
    PaymentDetailsV2,
    RefundDetails,
    SecurityDepositPayment,
    SecurityDepositAdjustment,
)
from sales.models import SalesPaymentDetails
from tailored.models import CustomerPayment
from expenses_v2.models import Expenses, ProductVariantExpense


class Command(BaseCommand):
    help = (
        "Backfill account_id in payment-related tables by payment method "
        "(cash -> cash account, upi/gpay/bank -> default bank account)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--shop-id", type=int, default=None, help="Run for one shop only")
        parser.add_argument("--dry-run", action="store_true", help="Preview only")
        parser.add_argument("--force", action="store_true", help="Run without confirmation")

    def handle(self, *args, **options):
        shop_id = options["shop_id"]
        dry_run = options["dry_run"]
        force = options["force"]

        shops = Shop.objects.filter(id=shop_id) if shop_id else Shop.objects.all()

        if not shops.exists():
            self.stdout.write(self.style.ERROR("No shops found for given input."))
            return

        self.stdout.write(f"Shops to process: {shops.count()}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN enabled. No DB writes."))

        if not dry_run and not force:
            confirm = input("Proceed with account backfill? (yes/no): ").strip().lower()
            if confirm != "yes":
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        total_updated = 0
        total_skipped_shops = 0

        for shop in shops.iterator():
            updated, skipped_reason = self._process_shop(shop, dry_run=dry_run)
            total_updated += updated

            if skipped_reason:
                total_skipped_shops += 1
                self.stdout.write(
                    self.style.WARNING(f"[Shop {shop.id}] Skipped: {skipped_reason}")
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"[Shop {shop.id}] Updated rows: {updated}")
                )

        self.stdout.write(self.style.SUCCESS("Backfill completed"))
        self.stdout.write(f"Total rows updated: {total_updated}")
        self.stdout.write(f"Shops skipped: {total_skipped_shops}")

    def _get_shop_accounts(self, shop):
        cash_account = (
            Account.objects.filter(
                shop=shop,
                deleted__isnull=True,
                is_active=True,
                account_type="cash",
            )
            .order_by("-is_default", "id")
            .first()
        )

        bank_accounts = Account.objects.filter(
            shop=shop,
            deleted__isnull=True,
            is_active=True,
            account_type="bank",
        )

        # Prefer any bank account with "default" in its name (case-insensitive).
        bank_account = (
            bank_accounts.filter(name__icontains="default")
            .order_by("-is_default", "id")
            .first()
        )

        # Fallback: first available active bank account for the shop.
        if not bank_account:
            bank_account = bank_accounts.filter(account_type="bank").order_by("id").first()

        return cash_account, bank_account

    def _process_shop(self, shop, dry_run=False):
        cash_account, bank_account = self._get_shop_accounts(shop)

        if not cash_account or not bank_account:
            missing = []
            if not cash_account:
                missing.append("cash account")
            if not bank_account:
                missing.append("bank account")
            return 0, "Missing " + " and ".join(missing)

        updates = []

        with transaction.atomic():
            # Booking: PaymentDetailsV2 (SafeDelete)
            updates.append(
                self._update_queryset(
                    PaymentDetailsV2.objects.filter(
                        booking__shop=shop,
                        deleted__isnull=True,
                        account__isnull=True,
                        payment_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    PaymentDetailsV2.objects.filter(
                        booking__shop=shop,
                        deleted__isnull=True,
                        account__isnull=True,
                        payment_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

            # Booking: RefundDetails (refund_method includes bank)
            updates.append(
                self._update_queryset(
                    RefundDetails.objects.filter(
                        booking__shop=shop,
                        account__isnull=True,
                        refund_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    RefundDetails.objects.filter(
                        booking__shop=shop,
                        account__isnull=True,
                        refund_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

            # Booking: SecurityDepositPayment
            updates.append(
                self._update_queryset(
                    SecurityDepositPayment.objects.filter(
                        booking__shop=shop,
                        account__isnull=True,
                        payment_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    SecurityDepositPayment.objects.filter(
                        booking__shop=shop,
                        account__isnull=True,
                        payment_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

            # Booking: SecurityDepositAdjustment (payment_method can be null)
            updates.append(
                self._update_queryset(
                    SecurityDepositAdjustment.objects.filter(
                        booking__shop=shop,
                        account__isnull=True,
                        payment_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    SecurityDepositAdjustment.objects.filter(
                        booking__shop=shop,
                        account__isnull=True,
                        payment_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

            # Sales: SalesPaymentDetails
            updates.append(
                self._update_queryset(
                    SalesPaymentDetails.objects.filter(
                        sale__shop=shop,
                        account__isnull=True,
                        payment_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    SalesPaymentDetails.objects.filter(
                        sale__shop=shop,
                        account__isnull=True,
                        payment_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

            # Tailor: CustomerPayment
            updates.append(
                self._update_queryset(
                    CustomerPayment.objects.filter(
                        order__shop=shop,
                        account__isnull=True,
                        payment_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    CustomerPayment.objects.filter(
                        order__shop=shop,
                        account__isnull=True,
                        payment_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

            # Expenses: Expenses (SafeDelete)
            updates.append(
                self._update_queryset(
                    Expenses.objects.filter(
                        shop=shop,
                        deleted__isnull=True,
                        account__isnull=True,
                        payment_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    Expenses.objects.filter(
                        shop=shop,
                        deleted__isnull=True,
                        account__isnull=True,
                        payment_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

            # Expenses: ProductVariantExpense (SafeDelete)
            updates.append(
                self._update_queryset(
                    ProductVariantExpense.objects.filter(
                        shop=shop,
                        deleted__isnull=True,
                        account__isnull=True,
                        payment_method="cash",
                    ),
                    cash_account.id,
                    dry_run,
                )
            )
            updates.append(
                self._update_queryset(
                    ProductVariantExpense.objects.filter(
                        shop=shop,
                        deleted__isnull=True,
                        account__isnull=True,
                        payment_method__in=["upi", "gpay", "bank"],
                    ),
                    bank_account.id,
                    dry_run,
                )
            )

        return sum(updates), None

    def _update_queryset(self, qs, account_id, dry_run):
        if dry_run:
            return qs.count()
        return qs.update(account_id=account_id)
