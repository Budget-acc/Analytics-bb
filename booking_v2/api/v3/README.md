# booking_v2/api/v3 — Booking API v3

## Overview

Version 3 of the booking API. All business logic lives in `booking_v2/v3/`; this package provides
structured split-module re-export shims under `booking_v2/api/v3/`.

## Key Characteristics

- Uses `shop.v1.utils.get_active_shop_id` for shop resolution.
- `BookingViewSet` is throttled with `ProductAvailabilityThrottle` (30 req/min).
- `ProductCursorPagination` (cursor-based, page_size=15, ordering=id) for product listing endpoints.
- `DayGroupedPagination` uses page_size=3.
- `BookingListSerializer.staff_name` is read directly from the `staff_name` model field (not a method override).

## ViewSets

### BookingViewSet

| Method | HTTP | URL pattern |
|---|---|---|
| `list` | GET | `/bookings/` |
| `retrieve` | GET | `/bookings/{id}/` |
| `create` | POST | `/bookings/` |
| `partial_update` | PATCH | `/bookings/{id}/` |
| `destroy` | DELETE | `/bookings/{id}/` |
| `get_payment_details` (@action) | GET | `/bookings/payment-details/{booking_id}/` |
| `available_products` (@action) | GET | `/bookings/available-products/` |
| `desktop_available_products` (@action) | GET | `/bookings/desktop-available-products/` |
| `old_bookings` (@action) | POST | `/bookings/old-bookings/` |

### ClientViewSet

| Method | HTTP | URL pattern |
|---|---|---|
| `list` | GET | `/clients/` |
| `retrieve` | GET | `/clients/{id}/` |
| `create` | POST | `/clients/` |
| `partial_update` | PATCH | `/clients/{id}/` |
| `destroy` | DELETE | `/clients/{id}/` |

## Standalone APIViews

- `BookingDashboardView` — booking dashboard stats
- `PaymentDetailsGroupedAPIView` — payments grouped by date
- `PendingBookingAmountAPIView` — pending booking amounts
- `PendingSecurityAmountAPIView` — pending security deposit amounts
- `BookingDetailAmountAPIView` — booking detail amount breakdown

## Import Paths

```python
from booking_v2.api.v3.serializers import BookingListSerializer, ClientDetailSerializer
from booking_v2.api.v3.utils.availability import check_product_variant_availability
from booking_v2.api.v3.utils.image_utils import copy_thumbnail_to_s3
from booking_v2.api.v3.views import BookingViewSet, ClientViewSet
```
