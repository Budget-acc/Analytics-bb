from django.utils.timezone import now
from booking_v2.models import ProductVariantAvailability


def _calculate_available_stock(self, variant):
    """
    Calculate available stock considering all future bookings across dates.
    Uses ProductVariantAvailability, which stores daily booked quantity.
    """
    today = now().date()

    # Get all upcoming daily bookings for this variant
    daily_bookings = ProductVariantAvailability.objects.filter(
        variant=variant,
        date__gte=today
    ).values_list("booked_quantity", flat=True)

    # Get the max booked quantity on any day
    max_booked_on_a_day = max(daily_bookings, default=0)

    # Available stock = total stock - max usage across future days
    return variant.stock - max_booked_on_a_day
