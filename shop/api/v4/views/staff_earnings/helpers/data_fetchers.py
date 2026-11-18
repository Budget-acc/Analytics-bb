from datetime import time

from django.db.models.functions import Coalesce
from django.utils.timezone import localtime
from django.db.models import Q, Sum, Count, Case, When, IntegerField, F, Value, DecimalField
from booking_v2.models import BookingV2
from sales.models import Sale
from tailored.models import TailorOrder, TailorOrderItem
from ERP.utils.custom_pagination import StandardPageNumberPagination
import logging

logs = logging.getLogger("api_requests")

# Earliest first; id breaks ties. Shared by monthly-transactions and the Excel rows.
BOOKINGS_ORDERING = ("completed_at", "id")
SALES_ORDERING = ("sale_date", "id")
TAILOR_ORDERS_ORDERING = ("completed_at", "id")


def _staff_bookings_earnings_qs(self, staff_id, year, month):
    """
    Completed bookings for the staff/month (filtered by completed_at), with the
    per-booking earnings annotations. Shared by the earnings-report aggregate
    and the Excel report rows, so both use identical logic by construction.
    """
    return BookingV2.objects.filter(
        staff_id=staff_id,
        shop_id=self.shop_id,
        completed_at__year=year,
        completed_at__month=month,
        booking_status="completed",
    ).annotate(
        total=Coalesce(F("total_amount"), Value(0)),
        advance=Coalesce(F("advance_amount"), Value(0)),
        discount=Coalesce(F("discount_amount"), Value(0)),
    ).annotate(
        pending=Case(
            When(total__gt=F("advance") + F("discount"),
                 then=F("total") - (F("advance") + F("discount"))),
            default=Value(0),
            output_field=IntegerField(),
        ),
        earned=F("advance"),
        is_pending=Case(
            When(total__gt=F("advance"), then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )


def _get_staff_bookings_data(self, staff_id, year, month):
    """
    Optimized: Get booking totals using aggregate instead of manual loop
    """
    qs = self._staff_bookings_earnings_qs(staff_id, year, month)

    agg = qs.aggregate(
        total_bookings_count=Count("id"),
        total_bookings_pending_amount=Coalesce(Sum("pending"), 0),
        total_bookings_earned_amount=Coalesce(Sum("earned"), 0),
        total_bookings_pending_count=Coalesce(Sum("is_pending"), 0),
    )

    return agg


def _pending_bookings_queryset(self, staff_id, year, month):
    """
    Base queryset for "pending" bookings: created in the given month
    (created_at), still booking_status="upcoming", and not
    delivery_status="cancelled". Shared by the earnings-report count/amount
    aggregate and the pending_bookings transaction-type list, so both stay
    consistent by construction.
    """
    return BookingV2.objects.filter(
        staff_id=staff_id,
        shop_id=self.shop_id,
        created_at__year=year,
        created_at__month=month,
        booking_status="upcoming",
    ).exclude(delivery_status="cancelled")


def _get_pending_bookings_data(self, staff_id, year, month):
    """
    Count and total pending amount for pending bookings (see
    _pending_bookings_queryset for the exact definition).
    """
    qs = self._pending_bookings_queryset(staff_id, year, month).annotate(
        total=Coalesce(F("total_amount"), Value(0)),
        paid=Coalesce(F("advance_amount"), Value(0)),
        discount=Coalesce(F("discount_amount"), Value(0)),
    ).annotate(
        pending=Case(
            When(total__gt=F("paid") + F("discount"),
                 then=F("total") - (F("paid") + F("discount"))),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )

    agg = qs.aggregate(
        pending_bookings_count=Count("id"),
        pending_bookings_amount=Coalesce(Sum("pending"), 0),
    )
    return agg


def _staff_sales_earnings_qs(self, staff_id, year, month):
    """
    Sales for the staff/month (filtered by sale_date), with the per-sale
    earning annotation. Shared by the earnings-report aggregate and the Excel
    report rows.
    """
    return Sale.objects.filter(
        staff_id=staff_id,
        shop_id=self.shop_id,
        sale_date__year=year,
        sale_date__month=month
    ).annotate(
        total=Coalesce(F("total_amount"), Value(0)),
        sale_discount=Coalesce(F("discount"), Value(0)),
        earning=Case(
            When(total__gt=F("discount"), then=F("total") - F("discount")),
            default=Value(0),
            output_field=IntegerField(),
        )
    )


def _get_staff_sales_data(self, staff_id, year, month):
    """
    Optimized sales aggregation
    """
    qs = self._staff_sales_earnings_qs(staff_id, year, month)

    agg = qs.aggregate(
        total_sales_count=Count("id"),
        total_sales_earning=Coalesce(Sum("earning"), 0),
    )

    return agg


def _staff_tailor_orders_qs(self, staff_id, year, month):
    """
    Completed tailor orders for the staff/month (filtered by completed_at),
    excluding orders converted into bookings. Shared by the earnings-report
    aggregate and the Excel report rows.
    """
    return TailorOrder.objects.filter(
        shop_id=self.shop_id,
        staff_id=staff_id,
        status="completed",
        created_booking__isnull=True,
        deleted__isnull=True,
        completed_at__year=year,
        completed_at__month=month,
    )


def _get_staff_tailor_data(self, staff_id, year, month):
    """
    Aggregate completed tailor orders for a staff member in a specific month.
    Excludes orders that have been converted into bookings (created_booking is set).
    """
    qs = self._staff_tailor_orders_qs(staff_id, year, month)

    agg = qs.aggregate(
        total_tailor_order_count=Count("id"),
        paid_amount=Sum("paid_amount"),
    )

    total_paid_amount = float(agg["paid_amount"] or 0)


    return {
        "total_tailor_order_count": agg["total_tailor_order_count"] or 0,
        "total_tailor_order_earnings": max(0, total_paid_amount),
    }


def _get_staff_monthly_transaction_rows(self, staff_id, year, month):
    """
    Every earnings transaction for the staff/month as plain rows, for the
    Excel report. Uses the same querysets (filters + earnings annotations)
    as the earnings-report aggregates, so the rows add up to its totals.

    The date of each row is the field the API filters that type by:
    bookings / tailor orders -> completed_at (shown in local time), sales ->
    sale_date. Sorted by that date, earliest first.

    Returns a list of dicts: date, invoice_id, transaction_type, amount.
    """
    rows = []

    bookings = self._staff_bookings_earnings_qs(staff_id, year, month).order_by(*BOOKINGS_ORDERING).values_list(
        "id", "shop_booking_id", "completed_at", "earned"
    )
    for pk, invoice, completed_at, earned in bookings:
        local_dt = localtime(completed_at)
        rows.append(((local_dt.date(), local_dt.time(), pk), invoice, "Booking", earned or 0))

    sales = self._staff_sales_earnings_qs(staff_id, year, month).order_by(*SALES_ORDERING).values_list(
        "id", "shop_sale_id", "sale_date", "earning"
    )
    for pk, invoice, sale_date, earning in sales:
        rows.append(((sale_date, time.min, pk), invoice, "Sales", earning or 0))

    tailor_orders = self._staff_tailor_orders_qs(staff_id, year, month).order_by(*TAILOR_ORDERS_ORDERING).values_list(
        "id", "shop_tailor_id", "completed_at", "paid_amount"
    )
    for pk, invoice, completed_at, paid_amount in tailor_orders:
        local_dt = localtime(completed_at)
        rows.append(((local_dt.date(), local_dt.time(), pk), invoice, "Custom Work", paid_amount or 0))

    # Earliest first; same-date rows stay together (key starts with the date).
    rows.sort(key=lambda r: r[0])
    return [
        {
            "date": sort_key[0],
            "invoice_id": invoice or "-",
            "transaction_type": transaction_type,
            "amount": amount,
        }
        for sort_key, invoice, transaction_type, amount in rows
    ]


def _paginate_bookings_response(self, bookings_qs):
    """
    Shared pagination + row serialization for booking-list transaction types
    (bookings, pending_bookings). Identical row shape for both.
    """
    paginator = StandardPageNumberPagination()
    paginated_bookings = paginator.paginate_queryset(bookings_qs, self.request)

    booking_data = []
    for booking in paginated_bookings:

        booked_items = [
            bp.product_variant.product.name
            if bp.product_variant and bp.product_variant.product
            else bp.product_name
            for bp in booking.booked_items.all()
            if (bp.product_variant and bp.product_variant.product) or bp.product_name
        ]

        booking_data.append({
            "id": booking.id,
            "shop_booking_id": booking.shop_booking_id,
            "client": booking.client_identity.client_name if booking.client_identity else "N/A",
            "pickup_date": booking.pickup_date.strftime("%d-%m-%Y %H:%M:%S"),
            "return_date": booking.return_date.strftime("%d-%m-%Y %H:%M:%S") if booking.return_date else None,
            "cooling_period_end": booking.cooling_period_end.strftime("%d-%m-%Y %H:%M:%S") if booking.cooling_period_end else None,
            "booking_status": booking.booking_status,
            "payment_status": booking.payment_status,
            "staff_name": booking.staff.name if booking.staff else booking.staff_name,
            "delivery_status": booking.delivery_status,
            "created_at": booking.created_at.strftime("%d-%m-%Y %H:%M:%S"),
            "total_amount": booking.total_amount,
            "discount_amount": booking.discount_amount or 0,
            "earning": max(0, booking.total_amount - (booking.discount_amount or 0)),
            "booked_items": booked_items,
        })

    return paginator.get_paginated_response(booking_data)


def _get_monthly_bookings(self, staff_id, month, year):
    """
    Get paginated bookings for staff in specific month/year
    """
    try:
        bookings_qs = (
            BookingV2.objects
            .select_related("client_identity", "staff")
            .prefetch_related("booked_items__product_variant__product")
            .filter(
                staff_id=staff_id,
                shop_id=self.shop_id,
                completed_at__year=year,
                completed_at__month=month,
                booking_status="completed",
            )
            .order_by(*BOOKINGS_ORDERING)
        )
        return self._paginate_bookings_response(bookings_qs)

    except Exception as e:
        logs.error(f"Database error fetching monthly bookings: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve booking data. Please try again later.",
            dev_message=f"Database error in bookings query: {str(e)}",
        )


def _get_monthly_pending_bookings(self, staff_id, month, year):
    """
    Get paginated pending bookings for staff (see _pending_bookings_queryset
    for the exact definition). Same row shape as _get_monthly_bookings.
    """
    try:
        bookings_qs = (
            self._pending_bookings_queryset(staff_id, year, month)
            .select_related("client_identity", "staff")
            .prefetch_related("booked_items__product_variant__product")
            .order_by("-created_at")
        )
        return self._paginate_bookings_response(bookings_qs)

    except Exception as e:
        logs.error(f"Database error fetching pending bookings: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve pending bookings data. Please try again later.",
            dev_message=f"Database error in pending bookings query: {str(e)}",
        )


def _get_monthly_sales(self, staff_id, month, year):
    """
    Get paginated sales for staff in specific month/year
    """
    try:
        sales_qs = Sale.objects.select_related(
            'client', 'staff'
        ).prefetch_related(
            'items__product_variant__product'
        ).filter(
            staff_id=staff_id,
            shop_id=self.shop_id,
            sale_date__year=year,
            sale_date__month=month
        ).order_by(*SALES_ORDERING)

        paginator = StandardPageNumberPagination()
        paginated_sales = paginator.paginate_queryset(sales_qs, self.request)

        sales_data = []
        for sale in paginated_sales:
            try:
                products = [
                    si.product_variant.product.name
                    for si in sale.items.all()
                    if si.product_variant and si.product_variant.product
                ]
            except Exception as e:
                logs.warning(f"Error getting sale items for sale {sale.id}: {str(e)}")
                products = []

            sales_data.append({
                'id': sale.id,
                'shop_sale_id': sale.shop_sale_id,
                'client_name': sale.client.client_name if sale.client else 'Walk-in Customer',
                'total_amount': sale.total_amount,
                'paid_amount': sale.paid_amount,
                'discount': sale.discount,
                'earning': max(0, sale.total_amount - sale.discount),
                'sale_date': sale.sale_date.strftime('%Y-%m-%d'),
                'created_at': sale.created_at.isoformat(),
                'description': sale.description or '',
                'staff_name': sale.staff.name if sale.staff else 'N/A',
                'products': products
            })

        return paginator.get_paginated_response(sales_data)

    except Exception as e:
        logs.error(f"Database error fetching monthly sales: {str(e)}", exc_info=True)
        return self.error_response(
            message='Unable to retrieve sales data. Please try again later.',
            dev_message=f'Database error in sales query: {str(e)}'
        )


def _get_monthly_tailor_orders(self, staff_id, month, year):
    """
    Get paginated completed tailor orders (not converted to bookings) for a staff
    member in a specific month/year. Mirrors the structure of the tailor order list view.
    """
    try:
        tailor_qs = (
            TailorOrder.objects
            .select_related("client", "staff", "shop")
            .prefetch_related("items")
            .filter(
                shop_id=self.shop_id,
                staff_id=staff_id,
                status="completed",
                created_booking__isnull=True,
                deleted__isnull=True,
                completed_at__year=year,
                completed_at__month=month,
            )
            .order_by(*TAILOR_ORDERS_ORDERING)
        )

        paginator = StandardPageNumberPagination()
        paginated_orders = paginator.paginate_queryset(tailor_qs, self.request)

        tailor_data = []
        for order in paginated_orders:
            items = ", ".join(
                item.product_name
                for item in order.items.all()
                if item.product_name
            )

            tailor_data.append({
                "id": order.id,
                "shop_tailor_id": order.shop_tailor_id,
                "client_name": order.client.client_name if order.client else None,
                "pickup_date": order.pickup_date.strftime("%d-%m-%Y %H:%M:%S") if order.pickup_date else None,
                "status": order.status,
                "customer_order_type": order.customer_order_type,
                "staff_name": order.staff.name if order.staff else None,
                "created_at": order.created_at.strftime("%d-%m-%Y %H:%M:%S") if order.created_at else None,
                "products": items,
            })

        return paginator.get_paginated_response(tailor_data)

    except Exception as e:
        logs.error(f"Database error fetching monthly tailor orders: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve tailor order data. Please try again later.",
            dev_message=f"Database error in tailor orders query: {str(e)}",
        )
