from .base import BookingViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .get_payment_details import get_payment_details
from .available_products import available_products
from .desktop_available_products import desktop_available_products
from .old_bookings import old_bookings
from .helpers import (
    _get_or_create_client,
    get_variants_availability,
    _cleanup_booking_documents_sync,
    get_booking_effective_window,
    _apply_product_variant_search,
)

BookingViewSet.list = list
BookingViewSet.retrieve = retrieve
BookingViewSet.create = create
BookingViewSet.partial_update = partial_update
BookingViewSet.destroy = destroy
BookingViewSet.get_payment_details = get_payment_details
BookingViewSet.available_products = available_products
BookingViewSet.desktop_available_products = desktop_available_products
BookingViewSet.old_bookings = old_bookings

BookingViewSet._get_or_create_client = _get_or_create_client
BookingViewSet.get_variants_availability = get_variants_availability
BookingViewSet._cleanup_booking_documents_sync = _cleanup_booking_documents_sync
BookingViewSet.get_booking_effective_window = get_booking_effective_window
BookingViewSet._apply_product_variant_search = _apply_product_variant_search

__all__ = ["BookingViewSet"]
