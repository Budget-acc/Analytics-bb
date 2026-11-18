# booking_v2/api/v4 — Booking API v4

## Overview

Version 4 of the booking API. All business logic lives in `booking_v2/v4/`; this package provides
structured split-module re-export shims under `booking_v2/api/v4/`.

## Key Characteristics

- Uses `shop.v3.utils.get_active_shop_id` for shop resolution.
- No throttling (no `ProductAvailabilityThrottle`).
- No `ProductCursorPagination`.
- `DayGroupedPagination` uses page_size=6 (increased from v3's 3).
- `BookingListSerializer` adds a `get_staff_name` method override (returns `obj.staff.name`).
- `BookingDesktopDashboardView` added (new in v4, not present in v3).
- `copy_thumbnail_to_s3` uses dynamic extension based on content-type (v4 improvement over v3).

## ViewSets

### BookingViewSet

| Method | HTTP | URL pattern |
|---|---|---|
| `list` | GET | `/bookings/` |
| `retrieve` | GET | `/bookings/{id}/` |
| `create` | POST | `/bookings/` |
| `partial_update` | PATCH | `/bookings/{id}/` |
| `destroy` | DELETE | `/bookings/{id}/` |

No `@action` decorators in v4's `BookingViewSet`.

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
- `BookingDesktopDashboardView` — desktop-optimized dashboard (new in v4)
- `PaymentDetailsGroupedAPIView` — payments grouped by date
- `PendingBookingAmountAPIView` — pending booking amounts
- `PendingSecurityAmountAPIView` — pending security deposit amounts
- `BookingDetailAmountAPIView` — booking detail amount breakdown

## Import Paths

```python
from booking_v2.api.v4.serializers import BookingListSerializer, ClientDetailSerializer
from booking_v2.api.v4.utils.availability import check_product_variant_availability
from booking_v2.api.v4.utils.image_utils import copy_thumbnail_to_s3
from booking_v2.api.v4.views import BookingViewSet, ClientViewSet, BookingDesktopDashboardView
```
