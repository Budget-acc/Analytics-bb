from django.utils import timezone

from booking_v2.models import ProductVariantAvailability


def calculate_available_stock(variant):
    """
    Calculate available stock considering future bookings
    """
    today = timezone.now().date()
    daily_bookings = ProductVariantAvailability.objects.filter(
        variant=variant, date__gte=today
    ).values_list("booked_quantity", flat=True)
    max_booked = max(daily_bookings, default=0)
    return variant.stock - max_booked
