# booking_v2/api/v5/serializers/__init__.py
from booking_v2.api.v5.serializers.client import ClientDetailSerializer
from booking_v2.api.v5.serializers.product import (
    ServiceDetailSerializer,
    BookedProductVariantSerializer,
)
from booking_v2.api.v5.serializers.booking import (
    BookingDetailSerializer,
    BookingAdditionalChargeSerializer,
    BookingDesktopListSerializer,
)
from booking_v2.api.v5.serializers.staff import StaffDetailSerializer

__all__ = [
    "ClientDetailSerializer",
    "ServiceDetailSerializer",
    "BookedProductVariantSerializer",
    "BookingDetailSerializer",
    "StaffDetailSerializer",
    "BookingAdditionalChargeSerializer",
    "BookingDesktopListSerializer",
]
