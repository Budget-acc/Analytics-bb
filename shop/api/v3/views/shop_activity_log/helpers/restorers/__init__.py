from .booking import restore_booking
from .product import restore_product
from .product_variant import restore_product_variant

RESTORER_REGISTRY = {
    "bookingv2": restore_booking,
    "product": restore_product,
    "productvariant": restore_product_variant,
}
