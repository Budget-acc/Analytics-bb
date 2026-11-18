from django.db.models.functions import Coalesce
from django.db.models import Q, Sum, Count, Case, When, IntegerField, F, Value, DecimalField
from booking_v2.models import BookingV2
from sales.models import Sale
from ERP.utils.custom_pagination import StandardPageNumberPagination
import logging

logs = logging.getLogger("api_requests")


def _get_staff_bookings_data(self, staff_id, year, month):
    """
    Optimized: Get booking totals using aggregate instead of manual loop
    """
    qs = BookingV2.objects.filter(
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
        is_earned=Case(
            When(total__lte=F("advance"), then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )

    agg = qs.aggregate(
        total_bookings_count=Count("id"),
        total_bookings_pending_amount=Coalesce(Sum("pending"), 0),
        total_bookings_earned_amount=Coalesce(Sum("earned"), 0),
        total_bookings_pending_count=Coalesce(Sum("is_pending"), 0),
        total_bookings_earned_count=Coalesce(Sum("is_earned"), 0),
    )

    return agg

def _get_staff_sales_data(self, staff_id, year, month):
    """
    Optimized sales aggregation
    """
    qs = Sale.objects.filter(
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

    agg = qs.aggregate(
        total_sales_count=Count("id"),
        total_sales_earning=Coalesce(Sum("earning"), 0),
    )

    return agg

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
            .order_by("-created_at")
        )

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

    except Exception as e:
        from django.db import DatabaseError
        logs.error(f"Database error fetching monthly bookings: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve booking data. Please try again later.",
            dev_message=f"Database error in bookings query: {str(e)}",
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
        ).order_by('-created_at')

        paginator = StandardPageNumberPagination()
        paginated_sales = paginator.paginate_queryset(sales_qs, self.request)

        # Serialize sales data
        sales_data = []
        for sale in paginated_sales:
            # Get products from SaleItem relationship
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
        from django.db import DatabaseError
        logs.error(f"Database error fetching monthly sales: {str(e)}", exc_info=True)
        return self.error_response(
            message='Unable to retrieve sales data. Please try again later.',
            dev_message=f'Database error in sales query: {str(e)}'
        )
