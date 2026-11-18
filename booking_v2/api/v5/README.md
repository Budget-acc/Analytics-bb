# booking_v2/api/v5 — Booking API v5

## Overview

Version 5 is the current production version with the most features. All business logic lives in
`booking_v2/v5/`; this package provides structured split-module re-export shims under
`booking_v2/api/v5/`.

## Key Characteristics

- Uses `shop.v3.utils.get_active_shop_id` for shop resolution.
- No `ClientViewSet` (client management was removed from this version).
- `BookingViewSet` supports `MultiPartParser`, `FormParser`, and `JSONParser`.
- `rental_duration_type` (daily/hourly) supported.
- `cooling_period_type` (before/after) for availability window calculations.
- Split payments validation (`_validate_payments`).
- Full async S3 document upload/cleanup via Celery tasks.
- WhatsApp invoice integration (`send_invoice` action).
- Security deposit adjustment ledger (`add_security_adjustment`, `delete_security_adjustment`).
- Partial return flow (`partial_return` action).
- Refund management (`add_refund`, `delete_refund` actions).
- `BookingAdditionalCharge`, `RefundDetails`, `SecurityDepositPayment`, `SecurityDepositAdjustment` models.
- `BookedProductVariant.return_status`, `returned_at`, `extended_until` fields.
- `LedgerSecurityAmountAPIView` replaces `PendingSecurityAmountAPIView`.

## BookingViewSet Actions

| Method | HTTP | URL pattern |
|---|---|---|
| `list` | GET | `/bookings/` |
| `retrieve` | GET | `/bookings/{id}/` |
| `create` | POST | `/bookings/` |
| `partial_update` | PATCH | `/bookings/{id}/` |
| `destroy` | DELETE | `/bookings/{id}/` |
| `desktop_list` (@action) | GET | `/bookings/desktop-list/` |
| `update_delivery_status` (@action) | POST | `/bookings/update-delivery-status/{booking_id}/` |
| `add_payment` (@action) | POST | `/bookings/add-payment/{booking_id}/` |
| `update_booking_status` (@action) | POST | `/bookings/update-booking-status/{booking_id}/` |
| `update_booking_details` (@action) | PATCH | `/bookings/update-booking-details/{booking_id}/` |
| `send_invoice` (@action) | POST | `/bookings/send-invoice/{booking_id}/` |
| `delete_payment` (@action) | DELETE | `/bookings/delete-payment/{payment_id}/` |
| `variant_availability` (@action) | GET | `/bookings/variant-availability/{identifier}/` |
| `add_refund` (@action) | POST | `/bookings/refund/{booking_id}/` |
| `delete_refund` (@action) | DELETE | `/bookings/refund/{booking_id}/{refund_id}/` |
| `add_security_adjustment` (@action) | POST | `/bookings/security-adjustment/{booking_id}/` |
| `delete_security_adjustment` (@action) | DELETE | `/bookings/delete-security-adjustment/{adjustment_id}/` |
| `partial_return` (@action) | POST | `/bookings/partial-return/{booking_id}/` |

## Standalone APIViews

- `LedgerSecurityAmountAPIView` — full security deposit ledger (bookings + tailor orders)
- `PendingBookingAmountAPIView` — pending amounts (bookings + tailor orders)
- `PaymentDetailsGroupedAPIView` — grouped payments (bookings + sales + tailor orders + cancellation penalties + security deductions)

## Import Paths

```python
from booking_v2.api.v5.serializers import (
    BookingDetailSerializer,
    BookedProductVariantSerializer,
    BookingDesktopListSerializer,
    StaffDetailSerializer,
    BookingAdditionalChargeSerializer,
)
from booking_v2.api.v5.utils.availability import check_product_variant_availability
from booking_v2.api.v5.utils.image_utils import copy_thumbnail_to_s3
from booking_v2.api.v5.views import BookingViewSet, LedgerSecurityAmountAPIView
```
