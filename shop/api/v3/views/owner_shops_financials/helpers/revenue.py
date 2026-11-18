from django.db.models.functions import Coalesce, ExtractMonth, Cast
from django.db.models import Q, Sum, F, Value, DecimalField, Subquery, OuterRef
from booking_v2.models import BookingV2, PaymentDetailsV2, RefundDetails
from sales.models import Sale, SalesPaymentDetails
from tailored.models import TailorOrder, CustomerPayment
from decimal import Decimal


def _get_payment_based_revenue(self, shop_ids, year, months):
    """
    Get revenue data based on payment dates.
    Matches the logic of transaction history endpoint.
    """
    ZERO_DECIMAL = Value(Decimal("0.00"), output_field=DecimalField())
    revenue_map = {}

    # 1. Booking Payments - grouped by payment date
    booking_payments = (
        PaymentDetailsV2.objects
        .filter(
            booking__shop_id__in=shop_ids,
            booking__deleted__isnull=True,
            booking__delivery_status__in=["booked", "ready to deliver", "delivered", "returned"],  # EXCLUDE cancelled
            created_at__year=year,
            created_at__month__in=months
        )
        .select_related('booking')
        .annotate(month=ExtractMonth("created_at"))
        .values("booking__shop_id", "month", "booking_id")
        .annotate(total_paid=Sum("payment_amount", output_field=DecimalField()))
    )

    for payment in booking_payments:
        shop_id = payment["booking__shop_id"]
        month = payment["month"]
        booking_id = payment["booking_id"]
        amount = Decimal(str(payment["total_paid"]))

        key = (shop_id, month)
        if key not in revenue_map:
            revenue_map[key] = {
                'bookings_amount': Decimal("0.00"),
                'bookings_ids': set(),
                'sales_amount': Decimal("0.00"),
                'sales_ids': set(),
                'tailor_amount': Decimal("0.00"),
                'tailor_ids': set()
            }

        revenue_map[key]['bookings_amount'] += amount
        revenue_map[key]['bookings_ids'].add(booking_id)

    # 2. Penalty amounts from cancelled bookings - grouped by refund date
    cancelled_bookings = (
        BookingV2.objects
        .filter(
            shop_id__in=shop_ids,
            deleted__isnull=True,
            delivery_status="cancelled"
        )
        .annotate(
            total_refund=Coalesce(
                Subquery(
                    RefundDetails.objects.filter(booking=OuterRef('pk'))
                    .values('booking')
                    .annotate(total=Sum('refunded_amount'))
                    .values('total')[:1]
                ),
                Value(0, output_field=DecimalField(max_digits=10, decimal_places=2))
            ),
            last_refund_time=Subquery(
                RefundDetails.objects.filter(booking=OuterRef('pk'))
                .order_by('-created_at')
                .values('created_at')[:1]
            )
        )
        .annotate(
            penalty_amount=Cast(F('advance_amount'), output_field=DecimalField(max_digits=10, decimal_places=2)) -
                        Cast(F('total_refund'), output_field=DecimalField(max_digits=10, decimal_places=2))
        )
        .filter(penalty_amount__gt=0)
        .filter(
            Q(
                last_refund_time__year=year,
                last_refund_time__month__in=months
            ) | Q(
                last_refund_time__isnull=True,
                created_at__year=year,
                created_at__month__in=months
            )
        )
        .values("shop_id", "last_refund_time", "id", "penalty_amount", "created_at")
    )

    for booking in cancelled_bookings:
        shop_id = booking["shop_id"]
        booking_id = booking["id"]
        penalty_amount = Decimal(str(booking["penalty_amount"]))

        if booking["last_refund_time"]:
            date_to_use = booking["last_refund_time"]
        else:
            date_to_use = booking["created_at"]

        month = date_to_use.month

        key = (shop_id, month)
        if key not in revenue_map:
            revenue_map[key] = {
                'bookings_amount': Decimal("0.00"),
                'bookings_ids': set(),
                'sales_amount': Decimal("0.00"),
                'sales_ids': set(),
                'tailor_amount': Decimal("0.00"),
                'tailor_ids': set()
            }

        # Add penalty amount to bookings revenue
        revenue_map[key]['bookings_amount'] += penalty_amount
        revenue_map[key]['bookings_ids'].add(f"penalty_{booking_id}")

    # 3. Sales Payments - grouped by payment date
    sales_payments = (
        SalesPaymentDetails.objects
        .filter(
            sale__shop_id__in=shop_ids,
            created_at__year=year,
            created_at__month__in=months
        )
        .select_related('sale')
        .annotate(month=ExtractMonth("created_at"))
        .values("sale__shop_id", "month", "sale_id")
        .annotate(total_paid=Sum("payment_amount", output_field=DecimalField()))
    )

    for payment in sales_payments:
        shop_id = payment["sale__shop_id"]
        month = payment["month"]
        sale_id = payment["sale_id"]
        amount = Decimal(str(payment["total_paid"]))

        key = (shop_id, month)
        if key not in revenue_map:
            revenue_map[key] = {
                'bookings_amount': Decimal("0.00"),
                'bookings_ids': set(),
                'sales_amount': Decimal("0.00"),
                'sales_ids': set(),
                'tailor_amount': Decimal("0.00"),
                'tailor_ids': set()
            }

        revenue_map[key]['sales_amount'] += amount
        revenue_map[key]['sales_ids'].add(sale_id)

    # 4. Tailor Order Payments - ALL types
    tailor_payments = (
        CustomerPayment.objects
        .filter(
            order__shop_id__in=shop_ids,
            order__deleted__isnull=True,
            created_at__year=year,
            created_at__month__in=months
        )
        .select_related('order')
        .annotate(month=ExtractMonth("created_at"))
        .values("order__shop_id", "month", "order_id")
        .annotate(total_paid=Sum("payment_amount", output_field=DecimalField()))
    )

    for payment in tailor_payments:
        shop_id = payment["order__shop_id"]
        month = payment["month"]
        order_id = payment["order_id"]
        amount = Decimal(str(payment["total_paid"]))

        key = (shop_id, month)
        if key not in revenue_map:
            revenue_map[key] = {
                'bookings_amount': Decimal("0.00"),
                'bookings_ids': set(),
                'sales_amount': Decimal("0.00"),
                'sales_ids': set(),
                'tailor_amount': Decimal("0.00"),
                'tailor_ids': set()
            }

        # Add ALL tailor payments to tailor_amount
        revenue_map[key]['tailor_amount'] += amount
        revenue_map[key]['tailor_ids'].add(order_id)

    return revenue_map

def _get_previous_period_revenue(self, shop_ids, year, month=None):
    """Get previous period revenue for growth calculation"""
    if month:
        # Previous month
        prev_year = year if month > 1 else year - 1
        prev_month = month - 1 if month > 1 else 12
        months = [prev_month]
        prev_year_filter = prev_year
    else:
        # Previous year (all months)
        prev_year_filter = year - 1
        months = list(range(1, 13))

    revenue_data = self._get_payment_based_revenue(shop_ids, prev_year_filter, months)

    # Aggregate by shop
    prev_revenue_map = {}
    for (shop_id, _), data in revenue_data.items():
        total = data['bookings_amount'] + data['sales_amount'] + data['tailor_amount']
        prev_revenue_map[shop_id] = prev_revenue_map.get(shop_id, Decimal("0.00")) + total

    return prev_revenue_map


def _get_previous_period_revenue_breakdown(self, shop_ids, year, month=None):
    """Get previous period revenue broken down by bookings and sales for growth calculation."""
    if month:
        prev_year = year if month > 1 else year - 1
        prev_month = month - 1 if month > 1 else 12
        months = [prev_month]
        prev_year_filter = prev_year
    else:
        prev_year_filter = year - 1
        months = list(range(1, 13))

    revenue_data = self._get_payment_based_revenue(shop_ids, prev_year_filter, months)

    prev_breakdown = {}
    for (shop_id, _), data in revenue_data.items():
        if shop_id not in prev_breakdown:
            prev_breakdown[shop_id] = {'bookings': Decimal("0.00"), 'sales': Decimal("0.00")}
        prev_breakdown[shop_id]['bookings'] += data['bookings_amount']
        prev_breakdown[shop_id]['sales'] += data['sales_amount'] + data['tailor_amount']

    return prev_breakdown
