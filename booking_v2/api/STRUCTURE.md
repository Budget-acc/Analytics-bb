# booking_v2/api — Package Structure

This package provides the refactored split-module layout for the `booking_v2` app.
Each version (`v3`, `v4`, `v5`) is organized into sub-packages for serializers, utils, and views.
All logic lives in the original `booking_v2/v3/`, `booking_v2/v4/`, and `booking_v2/v5/` modules;
the `api/` tree provides structured re-export shims and standalone implementations.

## Directory Layout

```
booking_v2/api/
├── __init__.py
├── STRUCTURE.md
├── admin/
├── v3/
│   ├── __init__.py
│   ├── urls.py               # Re-exports from booking_v2/v3/urls.py
│   ├── serializers/
│   │   ├── __init__.py       # Re-exports all serializer classes
│   │   ├── fields.py         # PreserveOrderJSONField
│   │   ├── client.py         # ClientDetailSerializer
│   │   ├── product.py        # BookedProductVariantSerializer, ServiceDetailSerializer,
│   │   │                     #   ProductVariantSerializer, ProductWithVariantsSerializer
│   │   └── booking.py        # ClientRequirementV2Serializer, BookingListSerializer,
│   │                         #   BookingDetailSerializer
│   ├── utils/
│   │   ├── __init__.py       # Re-exports all util functions
│   │   ├── availability.py   # check/add/remove_product_variant_availability
│   │   └── image_utils.py    # copy_thumbnail_to_s3, is_safe_url
│   └── views/
│       ├── __init__.py       # Re-exports all ViewSets and APIViews
│       ├── pagination.py     # ProductCursorPagination, DayGroupedPagination
│       ├── throttles.py      # ProductAvailabilityThrottle
│       ├── booking_viewset/
│       │   ├── __init__.py   # Exports BookingViewSet
│       │   ├── base.py       # BookingViewSetBase (initial + class attrs)
│       │   ├── list.py
│       │   ├── retrieve.py
│       │   ├── create.py
│       │   ├── partial_update.py
│       │   ├── destroy.py
│       │   ├── get_payment_details.py
│       │   ├── available_products.py
│       │   ├── desktop_available_products.py
│       │   ├── old_bookings.py
│       │   └── helpers/
│       │       └── __init__.py
│       ├── client_viewset/
│       │   ├── __init__.py   # Exports ClientViewSet
│       │   └── base.py       # ClientViewSetBase
│       ├── booking_dashboard_view.py
│       ├── payment_details_grouped_api_view.py
│       ├── pending_booking_amount_api_view.py
│       ├── pending_security_amount_api_view.py
│       └── booking_detail_amount_api_view.py
├── v4/
│   ├── __init__.py
│   ├── urls.py               # Re-exports from booking_v2/v4/urls.py
│   ├── serializers/          # Same layout as v3 (+ get_staff_name override in booking.py)
│   ├── utils/                # Same layout as v3 (v4 copy_thumbnail_to_s3 uses dynamic extension)
│   └── views/
│       ├── __init__.py
│       ├── pagination.py     # DayGroupedPagination (page_size=6)
│       ├── booking_viewset/  # list, retrieve, create, partial_update, destroy
│       ├── client_viewset/
│       ├── booking_dashboard_view.py
│       ├── booking_desktop_dashboard_view.py
│       ├── payment_details_grouped_api_view.py
│       ├── pending_booking_amount_api_view.py
│       ├── pending_security_amount_api_view.py
│       └── booking_detail_amount_api_view.py
└── v5/
    ├── __init__.py
    ├── urls.py               # Re-exports from booking_v2/v5/urls.py
    ├── serializers/
    │   ├── __init__.py
    │   ├── client.py         # ClientDetailSerializer (+ phone_1_e164, phone_2_e164)
    │   ├── product.py        # ServiceDetailSerializer, BookedProductVariantSerializer
    │   │                     #   (+ attributes, return_status, returned_at, extended_until)
    │   └── booking.py        # BookingDetailSerializer, StaffDetailSerializer,
    │                         #   BookingAdditionalChargeSerializer, BookingDesktopListSerializer
    ├── utils/
    │   ├── __init__.py
    │   ├── availability.py   # check/add/remove_product_variant_availability
    │   ├── image_utils.py    # Re-export shim → booking_v2/v5/utils/image_utils.py
    │   └── utils.py          # Re-export shim → booking_v2/v5/utils/utils.py
    └── views/
        ├── __init__.py
        ├── pagination.py     # DayGroupedPagination (page_size=6)
        ├── booking_viewset/
        │   ├── __init__.py   # Exports BookingViewSet
        │   ├── base.py
        │   ├── list.py, retrieve.py, create.py, partial_update.py, destroy.py
        │   ├── desktop_list.py, update_delivery_status.py, add_payment.py
        │   ├── update_booking_status.py, update_booking_details.py
        │   ├── send_invoice.py, delete_payment.py, variant_availability.py
        │   ├── add_refund.py, delete_refund.py
        │   ├── add_security_adjustment.py, delete_security_adjustment.py
        │   ├── partial_return.py
        │   └── helpers/__init__.py
        ├── ledger_security_amount_api_view.py
        ├── pending_booking_amount_api_view.py
        └── payment_details_grouped_api_view.py
```

## Key Differences Between Versions

| Feature | v3 | v4 | v5 |
|---|---|---|---|
| `get_active_shop_id` import | `shop.v1.utils` | `shop.v3.utils` | `shop.v3.utils` |
| `ProductAvailabilityThrottle` | Yes (30/min) | No | No |
| `ProductCursorPagination` | Yes | No | No |
| `DayGroupedPagination` page_size | 3 | 6 | 6 |
| `BookingViewSet` @actions | get_payment_details, available_products, desktop_available_products, old_bookings | None | desktop_list, update_delivery_status, add_payment, update_booking_status, update_booking_details, send_invoice, delete_payment, variant_availability, add_refund, delete_refund, add_security_adjustment, delete_security_adjustment, partial_return |
| Split payments | No | No | Yes |
| Document uploads (S3) | Basic | Basic | Full async |
| `rental_duration_type` | No | No | Yes |
| `cooling_period_type` | No | No | Yes |
| WhatsApp invoice | No | No | Yes |
| `ClientViewSet` | Yes | Yes | No |
| `BookingDesktopDashboardView` | No | Yes | No |
| `LedgerSecurityAmountAPIView` | No | No | Yes |
