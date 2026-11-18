# booking_v2/api/v5/utils/utils.py
# Re-export shim – all logic lives in booking_v2/v5/utils/utils.py.

from booking_v2.api.v5.utils.availability import (  # noqa: F401
    check_product_variant_availability,
    add_product_variant_availability,
    remove_product_variant_availability,
)
