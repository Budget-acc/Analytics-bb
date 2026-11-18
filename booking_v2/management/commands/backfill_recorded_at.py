"""
Management Command: backfill_recorded_at
========================================

PURPOSE
-------
Copies `created_at → recorded_at` for existing records where the values differ.
When `recorded_at` was added via migration, existing rows were stamped with the
migration run-time instead of their original entry time. This command corrects that.

New records created after the migration are unaffected — `auto_now_add` handles
them correctly at INSERT time.

MODELS
------
--bookings:
- booking_v2.BookingV2
- tailored.TailorOrder

--payments:
- booking_v2.PaymentDetailsV2
- tailored.CustomerPayment

--security (unconditional overwrite — no pre-check):
- booking_v2.RefundDetails
- booking_v2.SecurityDepositPayment
- booking_v2.SecurityDepositAdjustment
- tailored.TailorOrderRefund
- tailored.TailorOrderSecurityPayment
- tailored.TailorOrderSecurityDepositAdjustment

HOW TO RUN
----------
Update all models (default):
    python manage.py backfill_recorded_at

Update only payment models (PaymentDetailsV2 + CustomerPayment):
    python manage.py backfill_recorded_at --payments

Update only booking/order models (BookingV2 + TailorOrder):
    python manage.py backfill_recorded_at --bookings

Update only the six security/refund models (unconditional overwrite):
    python manage.py backfill_recorded_at --security

PERFORMANCE
-----------
- .only('id', 'created_at', 'recorded_at') — fetches 3 columns only
- .iterator(chunk_size=500) — server-side cursor, constant memory regardless of row count
- bulk_update in batches of 500 — one UPDATE per 500 rows, not one per row
- --bookings/--payments: filters to rows where recorded_at != created_at (idempotent)
- --security: updates ALL rows unconditionally (recorded_at field newly introduced)

VERIFICATION
------------
After running, confirm zero mismatches with:

    python manage.py shell -c "
    from booking_v2.models import BookingV2, PaymentDetailsV2
    from tailored.models import TailorOrder, CustomerPayment
    from django.db.models import F
    print('BookingV2        mismatches:', BookingV2.objects.exclude(recorded_at=F('created_at')).count())
    print('TailorOrder      mismatches:', TailorOrder.objects.exclude(recorded_at=F('created_at')).count())
    print('PaymentDetailsV2 mismatches:', PaymentDetailsV2.objects.exclude(recorded_at=F('created_at')).count())
    print('CustomerPayment  mismatches:', CustomerPayment.objects.exclude(recorded_at=F('created_at')).count())
    "

    python manage.py shell -c "
    from booking_v2.models import RefundDetails, SecurityDepositPayment, SecurityDepositAdjustment
    from tailored.models import TailorOrderRefund, TailorOrderSecurityPayment, TailorOrderSecurityDepositAdjustment
    from django.db.models import F
    for m in [RefundDetails, SecurityDepositPayment, SecurityDepositAdjustment,
              TailorOrderRefund, TailorOrderSecurityPayment, TailorOrderSecurityDepositAdjustment]:
        print(m.__name__, 'mismatches:', m.objects.exclude(recorded_at=F('created_at')).count())
    "

All counts should be 0.

SAFETY
------
- --bookings/--payments: idempotent — skips rows where recorded_at already equals created_at.
- --security: unconditional overwrite — safe because recorded_at was just introduced and
  had no meaningful prior value.
- Does not touch created_at or any other field.
- No destructive operations.
"""

from django.core.management.base import BaseCommand
from django.db.models import F, Q
from booking_v2.models import (
    BookingV2, PaymentDetailsV2,
    RefundDetails, SecurityDepositPayment, SecurityDepositAdjustment,
)
from tailored.models import (
    TailorOrder, CustomerPayment,
    TailorOrderRefund, TailorOrderSecurityPayment, TailorOrderSecurityDepositAdjustment,
)

BATCH_SIZE = 500


def backfill(model, stdout):
    # Only process rows where recorded_at differs from created_at
    qs = model.objects.filter(~Q(recorded_at=F('created_at'))).only('id', 'created_at', 'recorded_at')
    total = qs.count()

    if total == 0:
        stdout.write(f'  {model.__name__}: nothing to update.')
        return 0, 0

    updated = 0
    batch = []

    for obj in qs.iterator(chunk_size=BATCH_SIZE):
        obj.recorded_at = obj.created_at
        batch.append(obj)
        if len(batch) >= BATCH_SIZE:
            model.objects.bulk_update(batch, ['recorded_at'])
            updated += len(batch)
            batch = []

    if batch:
        model.objects.bulk_update(batch, ['recorded_at'])
        updated += len(batch)

    return total, updated


def backfill_unconditional(model, stdout):
    """Copy created_at → recorded_at for ALL rows with no precondition check."""
    qs = model.objects.only('id', 'created_at', 'recorded_at')
    total = qs.count()

    if total == 0:
        stdout.write(f'  {model.__name__}: nothing to update.')
        return 0, 0

    updated = 0
    batch = []

    for obj in qs.iterator(chunk_size=BATCH_SIZE):
        obj.recorded_at = obj.created_at
        batch.append(obj)
        if len(batch) >= BATCH_SIZE:
            model.objects.bulk_update(batch, ['recorded_at'])
            updated += len(batch)
            batch = []

    if batch:
        model.objects.bulk_update(batch, ['recorded_at'])
        updated += len(batch)

    return total, updated


class Command(BaseCommand):
    help = (
        'Backfill recorded_at from created_at for booking and payment models. '
        'Use --payments for payment models only, --bookings for booking/order models only, '
        '--security for the six security/refund models (unconditional overwrite), '
        'or no flag to update all models.'
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            '--payments',
            action='store_true',
            help='Update only PaymentDetailsV2 and CustomerPayment.',
        )
        group.add_argument(
            '--bookings',
            action='store_true',
            help='Update only BookingV2 and TailorOrder.',
        )
        group.add_argument(
            '--security',
            action='store_true',
            help=(
                'Update only RefundDetails, SecurityDepositPayment, SecurityDepositAdjustment, '
                'TailorOrderRefund, TailorOrderSecurityPayment, TailorOrderSecurityDepositAdjustment. '
                'Overwrites recorded_at unconditionally for all rows.'
            ),
        )

    def handle(self, *args, **options):
        run_all = not options['bookings'] and not options['payments'] and not options['security']
        run_bookings = options['bookings'] or run_all
        run_payments = options['payments'] or run_all
        run_security = options['security'] or run_all

        if run_bookings:
            for model in [BookingV2, TailorOrder]:
                self.stdout.write(f'Backfilling {model.__name__}...')
                total, updated = backfill(model, self.stdout)
                self.stdout.write(self.style.SUCCESS(f'  Done: {updated}/{total} rows updated.'))

        if run_payments:
            for model in [PaymentDetailsV2, CustomerPayment]:
                self.stdout.write(f'Backfilling {model.__name__}...')
                total, updated = backfill(model, self.stdout)
                self.stdout.write(self.style.SUCCESS(f'  Done: {updated}/{total} rows updated.'))

        if run_security:
            for model in [
                RefundDetails, SecurityDepositPayment, SecurityDepositAdjustment,
                TailorOrderRefund, TailorOrderSecurityPayment, TailorOrderSecurityDepositAdjustment,
            ]:
                self.stdout.write(f'Backfilling {model.__name__}...')
                total, updated = backfill_unconditional(model, self.stdout)
                self.stdout.write(self.style.SUCCESS(f'  Done: {updated}/{total} rows updated.'))

        self.stdout.write(self.style.SUCCESS('Backfill complete.'))
