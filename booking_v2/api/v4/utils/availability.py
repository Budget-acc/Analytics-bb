# booking_v2/api/v4/utils/availability.py
from datetime import timedelta, datetime
from django.db.models import Sum, Q
from booking_v2.models import ProductVariantAvailability
from rest_framework.response import Response
from rest_framework import status
from services.models import ProductVariant
from django.utils.timezone import localtime, make_aware
from booking_v2.models import BookedProductVariant
from ERP.utils.mixins import StandardResponseMixin


def check_product_variant_availability(variants, pickup_datetime, return_datetime, exclude_booking_id=None):
    """
    Check availability for a list of product variants between pickup_datetime and return_datetime.
    Each variant dict must have 'id' and 'quantity'.
    """

    for variant_info in variants:
        variant_id = variant_info["id"]
        quantity = variant_info["quantity"]

        variant = ProductVariant.objects.select_related("product").get(id=variant_id)
        stock = variant.stock
        product_name = variant.product.name
        attribute = variant.attribute or "default"

        existing_bookings = BookedProductVariant.objects.filter(
            product_variant=variant,
            booking__pickup_date__lt=return_datetime,
            booking__return_date__gt=pickup_datetime,
            booking__deleted__isnull=True
        )

        # Exclude the current booking if provided
        if exclude_booking_id:
            existing_bookings = existing_bookings.exclude(booking_id=exclude_booking_id)

        # Sum of quantity from those bookings
        existing = existing_bookings.aggregate(
            booked_quantity_sum=Sum("quantity")
        )["booked_quantity_sum"] or 0

        if existing + quantity > stock:

            def format_datetime(dt):
                if dt.tzinfo is not None:
                    return localtime(dt).strftime("%d-%m-%Y %H:%M:%S")
                else:
                    return dt.strftime("%d-%m-%Y %H:%M:%S")

            return Response(
                {
                    "status": "insufficient_stock",
                    "message": "Insufficient stock for the requested product variant.",
                    "data": {
                        "variant_id": variant_id,
                        "product_name": product_name,
                        "attribute": attribute,
                        "out_of_stock_between": {
                            "from": format_datetime(pickup_datetime),
                            "to": format_datetime(return_datetime),
                        }
                    }
                }, status=status.HTTP_400_BAD_REQUEST
            )

    return None


def add_product_variant_availability(variants, pickup_date_obj, return_date_obj):
    for variant_info in variants:
        variant_id = variant_info["id"]
        booked_quantity = variant_info["quantity"]
        current_date = pickup_date_obj

        while current_date <= return_date_obj:
            availability, created = ProductVariantAvailability.objects.get_or_create(
                variant_id=variant_id,
                date=current_date,
                defaults={"booked_quantity": booked_quantity},
            )
            if created:
                print(f"Created availability: Variant {variant_id}, Date {current_date}, Quantity {booked_quantity}")
            else:
                availability.booked_quantity += booked_quantity
                availability.save()
            current_date += timedelta(days=1)


def remove_product_variant_availability(variants, start_date, end_date):

    for variant_info in variants:
        variant_id = variant_info["id"]
        variant_quantity = variant_info["quantity"]
        current_date = start_date

        while current_date <= end_date:
            availability = ProductVariantAvailability.objects.filter(
                variant_id=variant_id, date=current_date
            ).first()
            if availability:
                availability.booked_quantity = max(0, availability.booked_quantity - variant_quantity)
                if availability.booked_quantity == 0:
                    availability.delete()
                else:
                    availability.save()
            else:
                print(f"No availability found to remove for Variant {variant_id} on {current_date}")
            current_date += timedelta(days=1)
