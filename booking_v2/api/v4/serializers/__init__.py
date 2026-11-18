from booking_v2.api.v4.serializers.client import ClientDetailSerializer
from booking_v2.api.v4.serializers.product import (
    BookedProductVariantSerializer,
    ServiceDetailSerializer,
    ProductVariantSerializer,
    ProductWithVariantsSerializer,
)
from booking_v2.api.v4.serializers.booking import (
    ClientRequirementV2Serializer,
    BookingListSerializer,
    BookingDetailSerializer,
)

__all__ = [
    "ClientDetailSerializer",
    "BookedProductVariantSerializer",
    "ServiceDetailSerializer",
    "ProductVariantSerializer",
    "ProductWithVariantsSerializer",
    "ClientRequirementV2Serializer",
    "BookingListSerializer",
    "BookingDetailSerializer",
]
