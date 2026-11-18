# booking_v2/api/v3/views/throttles.py
from rest_framework.throttling import UserRateThrottle


class ProductAvailabilityThrottle(UserRateThrottle):
    scope = 'product_availability'
