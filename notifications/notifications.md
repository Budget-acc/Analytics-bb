# Notification System (Push Notifications)

---

## Overview

The **Notification System** is designed to send push notifications to mobile users through **Firebase Cloud Messaging (FCM)**. It is one of the core supporting modules in the application. Its primary responsibility is to manage the complete lifecycle of push notifications — from device registration, through targeted delivery, to automatic daily reminders and expiry alerts.

Notifications are used in the Flutter mobile app to deliver real-time updates such as:

- Booking pickup reminders
- Booking return reminders
- Daily booking summary alerts
- Tailor pickup reminders
- Product expiry reminders (insurance, permit, pollution)
- Custom admin announcements
- Shop-specific operational alerts

The module connects directly with:

- **Authentication module** — to identify which user owns which device
- **Shop module** — to scope notifications per shop and validate shop membership
- **Booking module** — to trigger automatic booking-related reminders
- **Tailored module** — to trigger tailor pickup reminders
- **Services module** — to read `ServiceAttributeValue` expiry dates for product expiry reminders
- **Firebase Cloud Messaging** — the external service used to deliver push notifications to Android and iOS devices

The module handles:

- Registering and managing Firebase device tokens per user per shop
- Switching shop context on a device token when a user changes their active shop
- Deactivating device tokens on user logout
- Sending targeted push notifications from the admin panel
- Sending automated daily notifications via Celery background tasks
- Sending daily product expiry reminders (insurance, permit, pollution) via dedicated Celery tasks
- Cleaning up invalid or expired Firebase tokens automatically
- Logging every notification with creation and delivery timestamps for auditing
- Per-user notification history with read/click/delete tracking via `UserNotification`

---

## Folder Structure

```
notifications/
  models/                    ← Split model package
    __init__.py              ← Re-exports DeviceToken, Notification, UserNotification
    device_token.py
    notification.py
    user_notification.py
  api/
    v3/
      serializers.py
      tasks.py               ← Booking / tailor / daily Celery tasks
      urls.py                ← DRF router: device-tokens + notifications
      utils/
        firebase_utils.py    ← FCM delivery engine
        notification_service.py  ← create_shop_notification / create_user_notification
      views/
        device_token_viewset.py
        user_notifications/
          __init__.py        ← Composed UserNotificationViewSet
          base_user_notification_viewset.py
          user_notification_clicked.py
          user_notification_delete_all.py
          user_notification_destroy.py
          user_notification_read_all.py
    admin_v3/                ← Admin notification send API
  tasks/                     ← Expiry reminder Celery tasks
    __init__.py
    expiry_task_common.py    ← Shared engine for all expiry tasks
    insurance_expiry_notification_task.py
    permit_expiry_notification_task.py
    pollution_expiry_notification_task.py
  management/
    commands/
      cleanup_fcm_tokens.py
      test_firebase.py
```

---

## Models

The Notification module uses three database models — `DeviceToken`, `Notification`, and `UserNotification`. The models are stored in a split package under `notifications/models/` and are all re-exported from `notifications/models/__init__.py`.

---

### DeviceToken Model (`notifications/models/device_token.py`)

Stores the mapping between a user, their device, and the shop they are currently active in. Every FCM send ultimately queries this table.

| Field | Type | Description |
|-------|------|-------------|
| `user` | ForeignKey → `User` (CASCADE) | The authenticated user who owns this device. `related_name='device_tokens'`. |
| `shop` | ForeignKey → `Shop` (CASCADE, nullable) | The user's currently active shop. Notifications are scoped to this shop. |
| `token` | CharField (max_length=512, unique) | The FCM device token. Unique across the system — no two records share the same token. |
| `platform` | CharField (max_length=10) | `'android'` or `'ios'`. Used for platform-specific notification config. |
| `is_active` | BooleanField (default=True) | Whether this token should receive notifications. Set to `False` on logout (soft delete). |
| `failed_attempts` | IntegerField (default=0) | Tracks consecutive FCM delivery failures for this token. |
| `last_notification_sent` | DateTimeField (nullable) | Timestamp of the most recent successful notification sent to this token. |
| `created_at` | DateTimeField (auto_now_add) | When the token was first registered. |
| `updated_at` | DateTimeField (auto_now) | Last updated. Used to determine the user's most recently active device. |

**Key design decisions:**

- The `token` field is unique — if a user reinstalls the app, the existing record is updated via `update_or_create` rather than creating a duplicate.
- The `is_active` flag enables soft deletion. Token records are never hard-deleted — they are only deactivated.
- Invalid tokens reported by Firebase (`UNREGISTERED`, `INVALID_ARGUMENT`, `NOT_FOUND`) are automatically deactivated after each send operation.

---

### Notification Model (`notifications/models/notification.py`)

The central record for every notification created in the system, whether by an admin, a Celery task, or the `notification_service`.

| Field | Type | Description |
|-------|------|-------------|
| `title` | CharField (max_length=255) | The notification headline shown on the device. |
| `body` | TextField | The notification message body. |
| `data` | JSONField (nullable) | Optional custom JSON payload for the Flutter app (e.g., `{"redirect_to": "PRODUCT_DETAIL"}`). |
| `image_url` | URLField (nullable) | Optional S3 image URL for rich notifications. |
| `created_by` | ForeignKey → `User` (SET_NULL, nullable) | Admin user who triggered the notification. Preserved on user deletion. |
| `shop` | ForeignKey → `Shop` (CASCADE, nullable) | The shop this notification belongs to. `related_name='notifications'`. |
| `notification_type` | CharField (max_length=50) | One of: `booking`, `sale`, `expiry`, `stock`, `tailor`, `system`. |
| `priority` | CharField (max_length=10) | One of: `low`, `normal`, `high`. Used by FCM for delivery priority. |
| `content_type` | ForeignKey → `ContentType` (CASCADE, nullable) | Generic FK part 1 — the model type of the linked object (e.g., Product, Variant). |
| `object_id` | PositiveIntegerField (nullable) | Generic FK part 2 — the ID of the linked object. |
| `content_object` | GenericForeignKey | Resolved generic FK — e.g., a `Product` or `ProductVariant` instance. Used for deep-link redirect. |
| `created_at` | DateTimeField (auto_now_add) | When the notification was created. |
| `sent_at` | DateTimeField (nullable) | When the notification was sent via FCM. `null` until at least one FCM success. |
| `target` | CharField (max_length=50, default="all") | Audience scope: `all`, `shop`, or `user`. |

**`notification_type` values:**

| Value | Used for |
|-------|----------|
| `booking` | Booking pickup/return reminders |
| `sale` | Sale-related alerts |
| `expiry` | Product expiry reminders (insurance, permit, pollution) |
| `stock` | Stock-level alerts |
| `tailor` | Tailor pickup reminders |
| `system` | General platform messages |

**Key design decisions:**

- The `content_type` + `object_id` generic FK allows any model (Product, Variant, Booking, etc.) to be linked to a notification for deep-link redirect in the Flutter app.
- For expiry notifications the linked object is the `Product` or `ProductVariant` whose attribute is expiring.
- The `priority` field maps to `high` for items expiring within 2 days, `normal` for 3–7 days.

---

### UserNotification Model (`notifications/models/user_notification.py`)

Per-user record tracking whether a specific `Notification` was delivered, read, clicked, or deleted by each user. Every `Notification` produces one `UserNotification` row per eligible shop member.

| Field | Type | Description |
|-------|------|-------------|
| `user` | ForeignKey → `User` (CASCADE) | The user who received this notification. |
| `notification` | ForeignKey → `Notification` (CASCADE) | The parent notification. Deleting a `Notification` cascades to all its `UserNotification` rows. |
| `shop` | ForeignKey → `Shop` (CASCADE, nullable) | The shop context for this delivery. |
| `is_read` | BooleanField (default=False) | Whether the user has read this notification. |
| `is_deleted` | BooleanField (default=False) | Soft-delete flag. Deleted notifications are excluded from all list queries. |
| `push_status` | CharField | FCM delivery status: `pending`, `sent`, or `failed`. |
| `received_at` | DateTimeField (auto_now_add) | When this row was created (i.e., when the notification was dispatched). |
| `read_at` | DateTimeField (nullable) | When the user marked it as read. |
| `clicked_at` | DateTimeField (nullable) | When the user tapped the notification (recorded via the `clicked` endpoint). |

**Constraints:**

- `unique_together = ("user", "notification")` — prevents duplicate rows per user per notification.
- Indexes on `(user, -received_at)`, `(user, is_read)`, and `(notification)`.

**Key design decisions:**

- `UserNotification` rows are **never hard-deleted** by the user API — only `is_deleted=True` is set (soft delete). This preserves DB history.
- However, when an expiry `Notification` is replaced the next day, the old `Notification` is hard-deleted, which cascades and removes its `UserNotification` rows automatically. This keeps at most one expiry reminder per product per expiry type in the database at any time.

---

## Serializers (`notifications/api/v3/serializers.py`)

### DeviceTokenSerializer

Used by the Device Token Registration API to validate incoming Flutter app data.

- **Fields:** `token`, `platform`, `is_active`, `shop_id`
- `shop_id` is `IntegerField(required=False, write_only=True)` — used only for input; not a model field.
- `platform` validator enforces `'android'` or `'ios'` only.

### NotificationDetailSerializer

Read-only serializer for `Notification` content nested inside `UserNotificationSerializer`.

- **Fields:** `id`, `title`, `body`, `data`, `image_url`, `notification_type`, `priority`, `content_type`, `object_id`, `created_at`

### UserNotificationSerializer

Used by the user notification list API.

- **Fields:** `id`, `notification` (nested `NotificationDetailSerializer`), `shop_id`, `is_read`, `push_status`, `received_at`, `read_at`, `clicked_at`
- All fields are read-only.

---

## Device Token Registration API

- **Endpoint:** `POST /api/v3/notifications/device-tokens/register/`
- **Authentication:** `CutoffJWTAuthentication`
- **Permission:** `IsAuthenticated`

### Step-by-Step Logic

**Step 1 — Validate input.**
Runs data through `DeviceTokenSerializer`. On failure, logs `user_id`, `shop_id`, `client_ip`, `path`, and `errors` before returning a validation error: "Please check your input and try again."

**Step 2 — Resolve the active shop.**
- If `shop_id` is provided: validates integer format → checks `ShopUser` membership → retrieves `Shop`.
- If not provided: falls back to `get_active_shop_id(request)`. Returns error if no active shop found.

**Step 3 — Check for token conflicts.**
Queries `DeviceToken.objects.filter(token=token).exclude(user=request.user)`. Rejects if another user owns the token.

**Step 4 — Save or update.**
Uses `update_or_create(token=token, defaults={...})`. Returns: "Device token registered successfully."

---

## Update Shop API

- **Endpoint:** `POST /api/v3/notifications/device-tokens/update-shop/`
- **Authentication:** `CutoffJWTAuthentication`
- **Permission:** `IsAuthenticated`

Validates `token` and `shop_id` presence → validates integer format → checks `ShopUser` membership → verifies token belongs to `request.user` → updates `DeviceToken.shop` using `update_fields=["shop", "updated_at"]`.

---

## Remove Device Token API

- **Endpoint:** `POST /api/v3/notifications/device-tokens/remove/`
- **Authentication:** `CutoffJWTAuthentication`
- **Permission:** `IsAuthenticated`

Reads `token` from request body. Sets `is_active=False` using `.update()` filtered by both `user=request.user` and `token=token`. Soft-delete — record is preserved for history.

---

## User Notification API

A dedicated `UserNotificationViewSet` is registered at `notifications/` (alongside `device-tokens/`) by the same DRF router.

### Architecture

The ViewSet is composed from a base class and four mixin files:

```
UserNotificationViewSet
  ├── UserNotificationClickedMixin      → PATCH /{pk}/clicked/
  ├── UserNotificationReadAllMixin      → PATCH /read-all/
  ├── UserNotificationDestroyMixin      → DELETE /{pk}/
  ├── UserNotificationDeleteAllMixin    → DELETE /delete-all/
  └── BaseUserNotificationViewSet       → GET / (list), GET /unread-count/, PATCH /{pk}/read/
```

All endpoints use `CutoffJWTAuthentication` and `IsAuthenticated`.

### Shared Helpers (`BaseUserNotificationViewSet`)

- **`ALLOWED_TYPE_FILTERS`** — set built from `Notification.TYPE_CHOICES`. Used to validate the `type` query param.
- **`_parse_notification_id(pk)`** — validates the URL `pk` is an integer. Returns `(int_pk, None)` or `(None, error_response)`.
- **`_parse_bool_query(raw_value)`** — parses `true/false/1/0/yes/no` into Python `bool`. Returns `(bool_or_None, error_or_None)`.
- **`_base_queryset(request)`** — scoped to `user=request.user`, `is_deleted=False`, filtered by active shop. Uses `select_related("notification", "notification__content_type", "notification__shop")` to avoid N+1. Ordered by `-received_at`.

### Endpoints

| Method | URL | Action | Description |
|--------|-----|--------|-------------|
| GET | `/api/v3/notifications/notifications/` | `list` | Paginated notification list. Supports `type`, `is_read`, `page`, `page_size` query params. |
| GET | `/api/v3/notifications/notifications/unread-count/` | `unread_count` | Returns `{"unread_count": N}` for the current user and shop. |
| PATCH | `/api/v3/notifications/notifications/{pk}/read/` | `mark_read` | Marks a single notification as read. Race-condition safe (uses `.update()`). |
| PATCH | `/api/v3/notifications/notifications/{pk}/clicked/` | `mark_clicked` | Records `clicked_at` and auto-marks as read. |
| PATCH | `/api/v3/notifications/notifications/read-all/` | `mark_all_read` | Bulk-marks all unread notifications as read for current user and shop. |
| DELETE | `/api/v3/notifications/notifications/{pk}/` | `destroy` | Soft-deletes a single notification (`is_deleted=True`). |
| DELETE | `/api/v3/notifications/notifications/delete-all/` | `delete_all` | Soft-deletes all notifications for current user and shop. |

All mutation endpoints are wrapped in try/except with user-facing `message` and developer `dev_message` in error responses.

---

## Notification Service (`notifications/api/v3/utils/notification_service.py`)

### `create_shop_notification()`

Central function used by all modules (bookings, expiry tasks, etc.) to create and dispatch a notification.

**Parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `shop` | Shop instance | Required |
| `title` | str | Required |
| `body` | str | Required |
| `notification_type` | str | Default `"system"` |
| `priority` | str | `low` / `normal` / `high`. Default `"normal"` |
| `data` | dict | Extra JSON payload for Flutter app |
| `image_url` | str | Optional S3 image URL |
| `content_object` | model instance | Used to set `content_type` + `object_id` generic FK |
| `created_by` | User | Optional |
| `target` | str | Default `"shop"` |
| `exclude_user_ids` | list/set | User IDs to skip |
| `send_push` | bool | Whether to dispatch FCM after DB write. Default `True` |

**Flow (atomic):**
1. Resolves `content_type` + `object_id` from `content_object` if provided.
2. Creates `Notification` row with `sent_at=timezone.now()`.
3. Finds all active `ShopUser` records for the shop (excluding `exclude_user_ids`).
4. `bulk_create`s `UserNotification` rows with `ignore_conflicts=True`.
5. Outside the atomic block, calls `_dispatch_push()` to send FCM via `send_shop_notification()`.

Returns the `Notification` instance, or `None` on failure.

### `create_user_notification()`

Same flow but targets a single user instead of all shop members.

---

## Admin Notification API

- **Endpoint:** `POST /api/v3/notifications/admin/notifications/send/`
- **Authentication:** Standard `JWTAuthentication`
- **Permission:** `IsAdminUser`
- **Parsers:** `MultiPartParser`, `FormParser`

Validates input → resolves target IDs → uploads image to S3 (if provided) → saves `Notification` record → calls `send_targeted_notification()` → updates `sent_at` on success.

---

## Firebase Utilities (`notifications/api/v3/utils/firebase_utils.py`)

### Firebase App Initialization (`get_firebase_app`)

Initialized once as a global singleton using the service account path from env var `FIREBASE_SERVICE_ACCOUNT_PATH`.

### `_send_one_token(token, ...)`

Low-level single-token sender using `messaging.send()` (FCM HTTP v1). Returns `{"token", "ok", "resp", "error", "code"}`. Includes Android (high priority, channel `default`) and APNs (badge=1, sound=`default`) config.

### `send_to_tokens(tokens, title, body, data, image, max_workers=16)`

Concurrent sender using `ThreadPoolExecutor`. Steps:
1. Filters invalid tokens.
2. Initializes Firebase app.
3. Stringifies all `data` values (FCM requires `string:string`).
4. Sends concurrently (max `max_workers=32`).
5. Returns `{success_count, failure_count, invalid_tokens, failed_tokens, raw_results}`.

**Invalid token codes** that trigger deactivation: `UNREGISTERED`, `INVALID_ARGUMENT`, `NOT_FOUND`.

### `send_targeted_notification(target, ...)`

Main routing function:
- `target="all"` — all active tokens.
- `target="shop"` + `shop_id` — tokens filtered by shop; injects `shop_id` into data.
- `target="user"` + `user_id` — tokens filtered by user; optionally further filtered by shop.

After sending, calls `DeviceToken.objects.filter(token__in=invalid_tokens).update(is_active=False)` to deactivate stale tokens.

### Convenience Functions

| Function | Description |
|----------|-------------|
| `send_shop_notification(shop_id, ...)` | Sends to all active tokens for a shop |
| `send_user_notification(user_id, ...)` | Sends to a specific user's tokens |
| `send_user_current_shop_notification(user_id, ...)` | Sends to user's most-recently-updated token |
| `send_all_notifications(...)` | Sends to all active tokens platform-wide |
| `get_shop_token_count(shop_id)` | Returns active token count for a shop |
| `get_user_token_count(user_id, shop_id)` | Returns active token count for a user |
| `debug_device_tokens(shop_id, user_id)` | Logs token details for debugging |
| `test_single_notification(token, ...)` | Sends a test notification to one token |
| `debug_shop_notifications(shop_id)` | Checks shop + sends test to every token |

---

## Automated Notifications — Celery Tasks

### Celery Beat Schedule (ERP/settings.py)

| Schedule key | Task path | Time |
|---|---|---|
| `notify-daily-bookings` | `notifications.api.v3.tasks.notify_daily_bookings` | Configured in settings |
| `notify-daily-returns` | `notifications.api.v3.tasks.notify_daily_returns` | Configured in settings |
| `check-booking-return-reminders` | `notifications.api.v3.tasks.check_booking_return_reminders` | 08:15 |
| `check-subscription-renewal-reminders` | `notifications.api.v3.tasks.check_subscription_renewal_reminders` | 09:00 |
| `check-insurance-expiry-reminders` | `notifications.tasks.insurance_expiry_notification_task.check_insurance_expiry_notifications` | 09:10 |
| `check-permit-expiry-reminders` | `notifications.tasks.permit_expiry_notification_task.check_permit_expiry_notifications` | 09:20 |
| `check-pollution-expiry-reminders` | `notifications.tasks.pollution_expiry_notification_task.check_pollution_expiry_notifications` | 09:30 |

---

### Daily / Booking Tasks (`notifications/api/v3/tasks.py`)

#### `send_batch_notifications(tokens, ...)`
Helper that splits tokens into batches of 400 and calls `send_to_tokens()` per batch.

#### `notify_pickup(booking_id)`
Sends "Booking Pickup Reminder" to all active shop staff for a specific booking.

#### `notify_return(booking_id)`
Sends "Booking Return Reminder" to all active shop staff for a specific booking.

#### `notify_daily_bookings()`
Morning task. Aggregates today's `pickup_date` bookings per shop. For each shop with bookings and active tokens, sends "Good morning! Today's Bookings 🗓️". Returns `{successful_shops, failed_shops, total_shops_with_bookings}`.

#### `notify_daily_returns()`
Evening task. Same pattern using `return_date`. Sends "Reminder! Today's Returns ↩️". Data payload includes `redirect_to: "UPCOMING"`.

#### `notify_tailor_pickups()`
Morning task. Aggregates today's `TailorOrderItem` records with `assigned_tailor` per shop. Only iterates over shops with tailor pickups. Sends "Tailor Pickup Reminder". Data payload includes `redirect_to: "TAILOR_SCREEN"`. Returns `{successful_shops, failed_shops, total_shops_with_pickups}`.

---

### Expiry Reminder Tasks (`notifications/tasks/`)

Three dedicated Celery tasks send daily reminders for products whose insurance, permit, or pollution certificate is expiring within the next 7 days.

#### Common Engine (`expiry_task_common.py`)

**`EXPIRY_REMINDER_DAYS = 7`** — the reminder window. Notifications are sent for days 0 through 7 (expiry day inclusive).

**`_parse_expiry_date(raw_value, fallback_key)`**

Tolerant date parser supporting:
- ISO format (with `Z` replacement)
- `%Y-%m-%d`, `%d-%m-%Y`, `%d/%m/%Y`, `%m/%d/%Y`
- Dict payloads — tries keys: `expiry_date`, `date`, `{fallback_key}`, `value`

**`_delete_existing_expiry_notifications(...)`**

Hard-deletes all existing `Notification` rows matching `(shop, notification_type="expiry", content_type, object_id, data__expiry_type=expiry_type)` before a new one is created. Because `UserNotification` has `on_delete=CASCADE`, this also removes all associated `UserNotification` rows.

This guarantees the database always contains **at most one active expiry reminder per product per expiry type** at any point in time.

**`execute_expiry_notification_task(*, expiry_attr_key, expiry_type, title_prefix)`**

Core engine used by all three expiry tasks:

1. Queries `ServiceAttributeValue` filtered by `attribute__attribute_key=expiry_attr_key`.
2. Resolves the product: `attribute_value.product` or `variant.product`.
3. Gets the shop from `product.shop_service.shop`. Skips if shop inactive.
4. Parses the expiry date from `attribute_value.value` (JSONField).
5. Checks `0 <= days_left <= 7`. Skips if outside window.
6. Inside `transaction.atomic()`:
   - Calls `_delete_existing_expiry_notifications()` to remove the previous day's reminder.
   - Calls `create_shop_notification()` to create the new `Notification` + `UserNotification` rows and dispatch FCM push.
7. Returns stats: `{processed, created, failed, invalid_date, out_of_window, inactive_shop, no_target}`.

**Notification data payload for expiry:**
```json
{
  "redirect_to": "PRODUCT_DETAIL",
  "notification_type": "<expiry_type>",
  "expiry_type": "<expiry_type>",
  "expiry_date": "YYYY-MM-DD",
  "days_left": "N",
  "product_id": "...",
  "product_name": "...",
  "product_image": "...",
  "product_variant_id": "...",
  "object_id": "..."
}
```

**Priority:** `high` if `days_left <= 2`, otherwise `normal`.

#### Individual Task Files

| File | Task function | `expiry_attr_key` | `expiry_type` |
|------|--------------|-------------------|---------------|
| `insurance_expiry_notification_task.py` | `check_insurance_expiry_notifications` | `insurance_expiry` | `insurance_expiry` |
| `permit_expiry_notification_task.py` | `check_permit_expiry_notifications` | `permit_expiry` | `permit_expiry` |
| `pollution_expiry_notification_task.py` | `check_pollution_expiry_notifications` | `pollution_expiry` | `pollution_expiry` |

These attribute keys must match the `ServiceAttributeTemplate.attribute_key` values stored in the database.

---

## Management Commands

### `cleanup_fcm_tokens`
`python manage.py cleanup_fcm_tokens [--shop_id N] [--test-valid] [--dry-run]`

Validates all active FCM tokens. Deactivates any rejected by Firebase. Uses `test_single_notification()` per token.

### `test_firebase`
`python manage.py test_firebase`

Tests Firebase Admin SDK initialization using `FIREBASE_SERVICE_ACCOUNT_PATH`. Reports success or troubleshooting guidance.

---

## Push Notification Delivery Flow

1. A trigger occurs — admin API call, Celery Beat schedule, or event-driven task.
2. Target audience is determined (all / shop / user).
3. Active `DeviceToken` records are queried (`is_active=True`).
4. For shop/expiry notifications, the `Notification` and `UserNotification` rows are created atomically via `create_shop_notification()`.
5. FCM push is dispatched outside the atomic block via `send_shop_notification()` → `send_targeted_notification()` → `send_to_tokens()`.
6. Each token is sent concurrently via `ThreadPoolExecutor` using `messaging.send()` (HTTP v1).
7. Platform-specific config is included — Android (high priority, channel `default`) and APNs (badge=1, sound=`default`).
8. Any token returning `UNREGISTERED`, `INVALID_ARGUMENT`, or `NOT_FOUND` is automatically deactivated (`is_active=False`).
9. For expiry notifications: the previous day's `Notification` (and its `UserNotification` cascade) is hard-deleted before the new one is created, ensuring only one reminder per expiry event exists at any time.

---

## URL Configuration

| Path | Module | ViewSet |
|------|--------|---------|
| `/api/v3/notifications/device-tokens/` | `notifications.api.v3.urls` | `DeviceTokenViewSet` |
| `/api/v3/notifications/notifications/` | `notifications.api.v3.urls` | `UserNotificationViewSet` |
| `/api/v3/notifications/admin/` | `notifications.api.admin_v3.urls` | `AdminNotificationViewSet` |

---

## API Summary

| API | Method | Endpoint | Access | Description |
|-----|--------|----------|--------|-------------|
| Register Device Token | POST | `/api/v3/notifications/device-tokens/register/` | Authenticated | Registers or updates a Firebase device token on login |
| Remove Device Token | POST | `/api/v3/notifications/device-tokens/remove/` | Authenticated | Deactivates a device token on logout |
| Update Shop | POST | `/api/v3/notifications/device-tokens/update-shop/` | Authenticated | Updates the active shop on a device token when user switches shops |
| List Notifications | GET | `/api/v3/notifications/notifications/` | Authenticated | Paginated notification history with `type` and `is_read` filters |
| Unread Count | GET | `/api/v3/notifications/notifications/unread-count/` | Authenticated | Returns count of unread notifications |
| Mark Read | PATCH | `/api/v3/notifications/notifications/{pk}/read/` | Authenticated | Marks a single notification as read |
| Mark Clicked | PATCH | `/api/v3/notifications/notifications/{pk}/clicked/` | Authenticated | Records notification tap, auto-marks as read |
| Mark All Read | PATCH | `/api/v3/notifications/notifications/read-all/` | Authenticated | Bulk marks all notifications as read |
| Delete | DELETE | `/api/v3/notifications/notifications/{pk}/` | Authenticated | Soft-deletes a single notification |
| Delete All | DELETE | `/api/v3/notifications/notifications/delete-all/` | Authenticated | Soft-deletes all notifications |
| Send Notification | POST | `/api/v3/notifications/admin/notifications/send/` | Admin only | Manually sends a push notification to a target audience |

---

## Summary

The Notification System provides:

- **Device registration and management** — tokens are registered on login, updated on shop switch, and deactivated on logout. Conflict detection prevents cross-user token sharing.
- **Multi-shop push notification routing** — notifications are scoped to the user's currently active shop via `DeviceToken.shop`.
- **Per-user notification history** — `UserNotification` tracks read, click, soft-delete, and push status per user per notification.
- **Centralized notification service** — `create_shop_notification()` handles `Notification` + `UserNotification` creation and FCM dispatch atomically for all modules.
- **Admin-controlled notifications** — `IsAdminUser`-protected API for targeted or global pushes with optional image upload.
- **Automated daily reminders** — Celery tasks for morning booking pickups, evening returns, and tailor pickups.
- **Product expiry reminders** — Dedicated Celery tasks for insurance, permit, and pollution expiry dates, running daily at 09:10/09:20/09:30. Sends reminders for products expiring within 7 days.
- **Single-reminder guarantee** — For expiry notifications, the previous day's `Notification` is hard-deleted before the new one is created. The DB always holds at most one active expiry reminder per product per expiry type.
- **Token hygiene** — Tokens returning `UNREGISTERED`, `INVALID_ARGUMENT`, or `NOT_FOUND` from Firebase are automatically deactivated. `cleanup_fcm_tokens` management command available for manual validation.
- **Platform-specific delivery** — Android (high priority, notification channel) and iOS (APNs alert, badge, sound) configs included in every message.