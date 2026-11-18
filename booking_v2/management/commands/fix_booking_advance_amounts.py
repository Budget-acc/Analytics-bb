import logging
from decimal import Decimal, ROUND_DOWN

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import OuterRef, Subquery, Sum, Value, DecimalField
from django.db.models.functions import Coalesce

from booking_v2.models import BookingV2, PaymentDetailsV2, RefundDetails

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


class Command(BaseCommand):
    help = (
        "Recalculates and corrects the advance_amount (paid amount) for bookings.\n\n"
        "Formula: advance_amount = sum(non-deleted payments) - sum(refunds)\n\n"
        "Supports shop-scoped or global execution with --dry-run and --force options."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--shop-id",
            type=int,
            default=None,
            dest="shop_id",
            help="Restrict correction to a single shop. Omit to process all shops.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without writing anything.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        shop_id = options["shop_id"]
        dry_run = options["dry_run"]
        force = options["force"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database changes will be made."))
        elif not force:
            scope = f"shop_id={shop_id}" if shop_id else "all shops"
            self.stdout.write(
                self.style.WARNING(
                    f"This will update advance_amount for bookings ({scope}). "
                    "Run with --dry-run to preview first."
                )
            )
            confirm = input("Proceed? (yes/no): ").strip().lower()
            if confirm not in ("yes", "y"):
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        # --- Build base queryset ---
        # Use correlated subqueries — avoids Cartesian product inflation that
        # occurs when both payments and refunds are joined in one queryset.
        #
        # total_paid     = sum of non-deleted PaymentDetailsV2.payment_amount
        # total_refunded = sum of RefundDetails.refunded_amount
        # Both default to 0.00 when no related rows exist.
        _paid_sq = (
            PaymentDetailsV2.objects
            .filter(booking_id=OuterRef("pk"), deleted__isnull=True)
            .values("booking_id")
            .annotate(s=Sum("payment_amount", output_field=DecimalField(max_digits=12, decimal_places=2)))
            .values("s")
        )
        _refunded_sq = (
            RefundDetails.objects
            .filter(booking_id=OuterRef("pk"))
            .values("booking_id")
            .annotate(s=Sum("refunded_amount", output_field=DecimalField(max_digits=12, decimal_places=2)))
            .values("s")
        )

        _decimal_field = DecimalField(max_digits=12, decimal_places=2)

        qs = (
            BookingV2.objects
            .filter(deleted__isnull=True)
            .annotate(
                total_paid=Coalesce(
                    Subquery(_paid_sq, output_field=_decimal_field),
                    Value(Decimal("0.00"), output_field=_decimal_field),
                ),
                total_refunded=Coalesce(
                    Subquery(_refunded_sq, output_field=_decimal_field),
                    Value(Decimal("0.00"), output_field=_decimal_field),
                ),
            )
            .only("id", "advance_amount", "shop_id")
        )

        if shop_id:
            qs = qs.filter(shop_id=shop_id)

        total_scanned = 0
        total_mismatch = 0
        total_fixed = 0
        total_errors = 0
        total_delta = Decimal("0.00")

        # Process in keyset batches to avoid expensive large OFFSET scans.
        last_id = 0
        while True:
            batch = list(qs.filter(id__gt=last_id).order_by("id")[:BATCH_SIZE])
            if not batch:
                break
            last_id = batch[-1].id
            total_scanned += len(batch)

            to_update = []
            for booking in batch:
                correct_advance = max(
                    (booking.total_paid - booking.total_refunded).quantize(Decimal("0.01"), rounding=ROUND_DOWN),
                    Decimal("0.00"),
                )

                current = (booking.advance_amount or Decimal("0.00"))
                if not isinstance(current, Decimal):
                    current = Decimal(str(current))

                if current == correct_advance:
                    continue

                delta = correct_advance - current
                total_mismatch += 1
                total_delta += delta
                self.stdout.write(
                    f"  booking id={booking.id}  shop={booking.shop_id}  "
                    f"current={current}  correct={correct_advance}  "
                    f"delta={delta:+.2f}  "
                    f"(paid={booking.total_paid}, refunded={booking.total_refunded})"
                )

                if not dry_run:
                    booking.advance_amount = correct_advance
                    to_update.append(booking)

            if to_update:
                try:
                    with transaction.atomic():
                        BookingV2.objects.bulk_update(to_update, ["advance_amount"])
                    total_fixed += len(to_update)
                except Exception as exc:
                    total_errors += len(to_update)
                    logger.error(
                        "fix_booking_advance_amounts: bulk_update failed for batch ending_at_id=%s: %s",
                        last_id,
                        exc,
                        exc_info=True,
                    )
                    self.stdout.write(self.style.ERROR(f"  ERROR in batch ending at booking id {last_id}: {exc}"))

        # --- Summary ---
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done.  Scanned: {total_scanned}  |  "
                f"Mismatched: {total_mismatch}  |  "
                f"Fixed: {total_fixed}  |  "
                f"Errors: {total_errors}"
            )
        )
        self.stdout.write(f"  Total \u0394 advance_amount: {total_delta:+.2f}")
        if dry_run and total_mismatch:
            self.stdout.write(
                self.style.WARNING("Re-run without --dry-run to apply the above corrections.")
            )
