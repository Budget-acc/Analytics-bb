# booking_v2/api/v3/serializers/__init__.py
from booking_v2.api.v3.serializers.fields import PreserveOrderJSONField
from booking_v2.api.v3.serializers.client import ClientDetailSerializer
from booking_v2.api.v3.serializers.product import (
    BookedProductVariantSerializer,
    ServiceDetailSerializer,
    ProductVariantSerializer,
    ProductWithVariantsSerializer,
)
from booking_v2.api.v3.serializers.booking import (
    ClientRequirementV2Serializer,
    BookingListSerializer,
    BookingDetailSerializer,
)

__all__ = [
    "PreserveOrderJSONField",
    "ClientDetailSerializer",
    "BookedProductVariantSerializer",
    "ServiceDetailSerializer",
    "ProductVariantSerializer",
    "ProductWithVariantsSerializer",
    "ClientRequirementV2Serializer",
    "BookingListSerializer",
    "BookingDetailSerializer",
]
