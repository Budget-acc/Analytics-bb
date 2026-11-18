import logging
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from booking_v2.models import SecurityDepositPayment
from finance.api.v3.utils import update_daily_balance
from finance.models import LedgerEntry

logger = logging.getLogger(__name__)

CORRUPT_DATE = date(2026, 4, 25)


class Command(BaseCommand):
    help = (
        "Fix corrupted created_at timestamps in SecurityDepositPayment (and their "
        "linked LedgerEntry records) caused by the April-25-2026 booking-update bug.\n\n"
        "Branch A (--shop-id supplied): for the given shop, fix SDPs created on 2026-04-25 "
        "where created_at does not match booking.created_at.\n\n"
        "Branch B (all other shops): fix SDPs created on 2026-04-25 across all other shops "
        "where created_at does not match booking.created_at NOR pickup_date."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--shop-id",
            type=int,
            default=None,
            dest="shop_id",
            help="Special shop to process under Branch A (April-25 entries, unconditional fix).",
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
            self.stdout.write(
                self.style.WARNING(
                    "This will update SecurityDepositPayment and LedgerEntry records "
                    "and recalculate DailyBalances. Run with --dry-run to preview first."
                )
            )
            confirm = input("Proceed? (yes/no): ").strip().lower()
            if confirm not in ("yes", "y"):
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        total_found = 0
        total_fixed = 0
        total_skipped = 0
        total_errors = 0

        # ---------------------------------------------------------------
        # Branch A — specific shop, April-25 entries with validation
        # ---------------------------------------------------------------
        if shop_id:
            self.stdout.write(
                f"\n[Branch A] Processing shop_id={shop_id} — "
                f"SDPs created on {CORRUPT_DATE} with booking-date check ..."
            )
            qs_a = (
                SecurityDepositPayment.objects
                .select_related("booking", "booking__shop")
                .filter(booking__shop_id=shop_id, created_at__date=CORRUPT_DATE)
            )
            for sdp in qs_a:
                booking_ts = sdp.booking.created_at

                # Branch A rule: specific shop must match booking created_at only.
                booking_date_matches = sdp.created_at == booking_ts
                needs_fix = not booking_date_matches

                if not needs_fix:
                    total_skipped += 1
                    self.stdout.write(
                        f"  [A] [SKIPPED] SDP id={sdp.id} — matches booking date "
                        f"({sdp.created_at.date()})"
                    )
                    continue

                total_found += 1
                correct_ts = booking_ts
                fixed, skipped, errored = self._fix_record(
                    sdp, correct_ts, dry_run, branch="A"
                )
                total_fixed += fixed
                total_skipped += skipped
                total_errors += errored

        # ---------------------------------------------------------------
        # Branch B — all other shops, April-25 entries with validation
        # ---------------------------------------------------------------
        self.stdout.write(
            "\n[Branch B] Processing all shops"
            + (f" (excluding shop_id={shop_id})" if shop_id else "")
            + " — SDPs created on " + str(CORRUPT_DATE) + " with mismatch check ..."
        )
        qs_b = (
            SecurityDepositPayment.objects
            .select_related("booking", "booking__shop")
            .filter(created_at__date=CORRUPT_DATE)
        )
        if shop_id:
            qs_b = qs_b.exclude(booking__shop_id=shop_id)

        for sdp in qs_b:
            booking_ts = sdp.booking.created_at
            pickup_dt = sdp.booking.pickup_date

            # Check if SDP date matches booking date OR pickup date
            booking_date_matches = sdp.created_at == booking_ts
            pickup_date_matches = pickup_dt and sdp.created_at.date() == pickup_dt.date()

            # Only update if matches NEITHER booking.created_at NOR pickup_date
            needs_fix = not booking_date_matches and not pickup_date_matches

            if not needs_fix:
                total_skipped += 1
                match_type = "booking date" if booking_date_matches else "pickup date"
                self.stdout.write(
                    f"  [B] [SKIPPED] SDP id={sdp.id} — matches {match_type} "
                    f"({sdp.created_at.date()})"
                )
                continue

            total_found += 1
            correct_ts = booking_ts
            fixed, skipped, errored = self._fix_record(
                sdp, correct_ts, dry_run, branch="B"
            )
            total_fixed += fixed
            total_skipped += skipped
            total_errors += errored

        # ---------------------------------------------------------------
        # Summary
        # ---------------------------------------------------------------
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done.  Found: {total_found}  |  "
                f"Fixed: {total_fixed}  |  "
                f"Skipped: {total_skipped}  |  "
                f"Errors: {total_errors}"
            )
        )

    def _fix_record(self, sdp, correct_ts, dry_run, branch="?"):
        """
        Fix one SecurityDepositPayment record and its linked LedgerEntry.
        Returns (fixed, skipped, errored) counts — each 0 or 1.
        """
        old_date = sdp.created_at.date()
        new_date = correct_ts.date()
        account_name = sdp.account.name if sdp.account_id else "—"

        prefix = "[DRY RUN]" if dry_run else "Fixed    "
        self.stdout.write(
            f"  [{branch}] {prefix}  SDP id={sdp.id}  "
            f"booking={sdp.booking_id}  "
            f"{old_date} → {new_date}  "
            f"account={account_name}"
        )

        if dry_run:
            return 0, 1, 0

        try:
            with transaction.atomic():
                # 1. Fix SecurityDepositPayment
                sdp.created_at = correct_ts
                sdp.save(update_fields=["created_at"])

                # 2. Fix the active LedgerEntry (if it shares the corrupted date)
                entry = LedgerEntry.objects.filter(
                    reference_id=sdp.id,
                    reference_type="SecurityDepositPayment",
                    deleted__isnull=True,
                ).first()
                if entry and entry.created_at.date() == old_date:
                    entry.created_at = correct_ts
                    entry.save(update_fields=["created_at"])
                    self.stdout.write(
                        f"           └─ LedgerEntry id={entry.id} also fixed."
                    )

                # 3. Recalculate DailyBalance for affected account + both dates
                if sdp.account_id:
                    update_daily_balance(
                        shop=sdp.booking.shop_id,
                        account=sdp.account_id,
                        date=old_date,
                    )
                    if new_date != old_date:
                        update_daily_balance(
                            shop=sdp.booking.shop_id,
                            account=sdp.account_id,
                            date=new_date,
                        )

            return 1, 0, 0

        except Exception as exc:
            logger.error(
                "fix_security_deposit_timestamps: failed for SDP id=%s: %s",
                sdp.id,
                exc,
                exc_info=True,
            )
            self.stdout.write(
                self.style.ERROR(
                    f"  [{branch}] ERROR   SDP id={sdp.id}: {exc}"
                )
            )
            return 0, 0, 1
