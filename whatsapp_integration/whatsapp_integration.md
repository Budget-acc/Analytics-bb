# WhatsApp Integration Module

---

## Overview

The **WhatsApp Integration Module** enables the system to send automated WhatsApp messages to customers. It is a supporting module that handles all outbound customer communication through the WhatsApp Business API.

The application integrates with a third-party WhatsApp messaging provider called **WACTO Live**, which allows sending messages using predefined templates. The system does not send free-form messages — every message is based on a **template that has been pre-approved on the WACTO platform**.

These messages are used to notify customers about important events such as:

- Booking invoice delivery (with PDF attachment)
- Sales invoice delivery (with PDF attachment)
- Pickup reminders (text only)
- Return reminders (text only)

All messages are sent **asynchronously using Celery background tasks** to ensure the main API remains fast and responsive. When a booking or sale is created, the API triggers a Celery task and returns immediately — the actual WhatsApp message is sent in the background by the Celery worker.

The module connects directly with:

- **Booking module** — booking data is used to generate booking invoice PDFs and trigger pickup/return reminders
- **Sales module** — sale data is used to generate sale invoice PDFs
- **Subscription module** — controls whether a shop has WhatsApp messaging enabled and enforces message sending limits
- **Services module (S3)** — PDFs are uploaded to AWS S3 to generate public URLs (WACTO requires public document URLs)
- **PDF generation engine (WeasyPrint)** — generates professional invoice PDFs with full Malayalam font support

The module handles:

- Generating booking and sales invoice PDFs using WeasyPrint
- Uploading PDFs to AWS S3 to get publicly accessible URLs
- Sending booking invoice messages with PDF attachments via WhatsApp
- Sending sales invoice messages with PDF attachments via WhatsApp
- Sending pickup reminder text messages via WhatsApp
- Sending return reminder text messages via WhatsApp
- Validating and formatting phone numbers to international E.164 format
- Checking subscription-based feature access before sending any message
- Tracking and enforcing per-shop message sending limits with atomic database operations
- Rolling back usage counters when a message fails to send
- Preventing duplicate reminders on same-day pickup-and-return bookings

---

## WhatsApp Service Provider

The system currently uses the **WACTO Live WhatsApp service** as its messaging provider. WACTO provides the WhatsApp Business API that the backend calls to deliver messages to customers.

The connection to WACTO is configured using two environment variables:

- `WACTO_BASE_URL` — the base URL of the WACTO API
- `WACTO_API_KEY` — the authentication token for the WACTO API

These values can also be configured in Django settings under the `WHATSAPP_CONFIG` dictionary as `BASE_URL` and `TOKEN`. The environment variables take precedence over the settings values.

The WACTO API works as follows:

- Messages are sent via a `POST` request to `https://{base_url}/message/send-message?token={api_key}`
- Each request specifies a pre-approved template name with dynamic parameters
- For document-type messages (invoices), WACTO requires a **publicly accessible URL** pointing to the document — it does not accept base64 data or file uploads directly in the message payload
- The API responds with HTTP 200 on success, with the response body containing delivery details

---

## Message Templates

WhatsApp messages are sent using **template-based messaging**. Templates must first be created and approved on the WACTO platform before the backend can use them. The backend references each template by its name and passes dynamic parameters at send time.

Each template is defined by:

| Field | Description |
|-------|-------------|
| Template Name | The unique name of the message template as registered on WACTO |
| Language Code | The language used in the template (currently `"en"` for English) |
| Language Policy | Set to `"deterministic"` — the exact language is specified, not auto-detected |
| Template Type | The type of template — `"template"` in the API payload |
| Components | The template structure — header (for documents), body (for text parameters), and button (for URL buttons) |

The system currently uses four templates:

| Template Name | Purpose | Has Document Attachment |
|---------------|---------|------------------------|
| `rental_invoice_5` | Booking confirmation with PDF invoice | Yes |
| `sale_invoice_2` | Sales confirmation with PDF invoice | Yes |
| `pickup_reminder_2` | Pickup reminder text message | No |
| `return_reminder_1` | Return reminder text message | No |

---

## Template Parameters and Phone Number Formatting

### Parameters Sent per Template

Each template receives dynamic parameters that are inserted into the pre-approved template text. The parameters vary by template type:

**Booking Invoice (`rental_invoice_5`):**

- **Header component:** Document link (public S3 URL to the PDF) and filename
- **Body component:** Customer name, shop name, formatted shop phone number
- **Button component:** Shop phone number (used in the "Call Shop" button URL)

**Sales Invoice (`sale_invoice_2`):**

- **Header component:** Document link (public S3 URL to the PDF) and filename
- **Body component:** Customer name, shop name
- **Button component:** Shop phone number (used in the "Call Shop" button URL)

**Pickup Reminder (`pickup_reminder_2`):**

- **Body component:** Customer name, shop name, formatted shop phone number
- **Button component:** Shop phone number

**Return Reminder (`return_reminder_1`):**

- **Body component:** Customer name, shop name, formatted shop phone number
- **Button component:** Shop phone number

### Phone Number Formatting

Phone numbers must be formatted specifically for the WACTO WhatsApp API. The API expects numbers in **international format without a "+" prefix**.

The `_format_phone_number()` method in the `WhatsAppService` class handles this formatting:

- If the number starts with `+` → remove the `+` and return the rest (e.g., `+919876543210` → `919876543210`)
- If the number is 10 digits → assume it is an Indian number and prepend `91` (e.g., `9876543210` → `919876543210`)
- If the number is 11 digits starting with `0` → remove the leading `0` and prepend `91` (e.g., `09876543210` → `919876543210`)
- If the number already has a country code without `+` → return as-is (e.g., `919876543210` → `919876543210`)

### Phone Number Validation (E.164)

Before phone numbers are even passed to the WhatsApp service, they are validated using the `validate_and_format_phone()` function in the tasks module. This function uses the `phonenumbers` Python library for rigorous validation:

- Strips all non-digit characters (spaces, dashes, brackets)
- Handles multiple input formats: `+919876543210`, `9876543210`, `0919876543210`, `919876543210`, `00919876543210`
- Parses the number using `phonenumbers.parse()` and validates it with `phonenumbers.is_valid_number()`
- Returns the number in proper E.164 format (e.g., `+919876543210`)
- If the number is invalid — returns an error message explaining why

This two-stage formatting ensures that:

1. The `validate_and_format_phone()` function validates the number and converts it to E.164 with the `+` prefix
2. The `_format_phone_number()` method then removes the `+` prefix for WACTO compatibility

---

## Background Message Processing

All WhatsApp messages are sent using **Celery background tasks**. This architecture is critical because:

- Generating a PDF invoice can take several seconds (especially with image downloads and WeasyPrint rendering)
- Uploading the PDF to S3 adds network latency
- The WACTO API call itself has a 30-second timeout
- None of these delays should block the user's API response

The message sending workflow for invoice messages is:

1. The main API (booking creation or sale creation) completes and returns a response to the user
2. The API triggers a Celery task (e.g., `send_booking_invoice_task.delay(booking_id)`)
3. The Celery worker picks up the task and executes it:
   - Fetches the booking/sale with all related objects
   - Validates the client and shop phone numbers
   - Checks WhatsApp usage limits
   - Generates the PDF using WeasyPrint
   - Uploads the PDF to AWS S3
   - Sends the WhatsApp message via WACTO
   - Updates the message counter
4. If the task fails due to a transient error (e.g., network timeout), it retries up to 3 times with a 60-second delay between retries
5. If the task fails due to a validation error (e.g., invalid phone number), it does **not** retry — the error is logged and the task is marked as failed

For reminder messages (pickup/return), the workflow is slightly different:

1. Celery Beat triggers the task at the scheduled time (e.g., every morning)
2. The task queries all bookings for today, groups them by shop
3. For each shop, it checks feature access and validates the shop phone number
4. For each booking in the shop, it validates the client phone, checks usage limits, and sends the message
5. Failed messages are counted and logged, but the task continues processing remaining bookings

---

## Feature Access Control

Before sending any WhatsApp message, the system performs several checks to ensure the shop is authorized to send messages. This prevents unauthorized usage and enforces subscription limits.

### Subscription Check

The system verifies:

- The shop has a `ShopSubscription` record
- The subscription is currently active (not expired)
- The subscription's plan includes the required feature

### Feature Flags

Two separate feature codes control WhatsApp messaging:

| Feature Code | Controls | Used By |
|-------------|----------|---------|
| `WHATSAPP_MESSAGE` | Booking invoices and sales invoices | `send_booking_invoice_task`, `send_sale_invoice_task` |
| `BOOKING_REMINDERS_WHATSAPP` | Pickup reminders and return reminders | `send_pickup_ready_notifications`, `send_return_reminder_notifications` |

Additionally, the Django settings flag `WHATSAPP_REMINDERS_ENABLED` acts as a **global kill switch** for all reminder notifications. If this setting is `False`, no pickup or return reminders are sent regardless of individual shop feature access.

### How Feature Access Is Checked

For invoice messages (booking/sale), the `check_and_update_whatsapp_usage()` function handles both the feature access check and the usage limit check in a single atomic operation.

For reminder messages (pickup/return), the task first checks `settings.WHATSAPP_REMINDERS_ENABLED`, then uses `has_feature_access(shop_user, "BOOKING_REMINDERS_WHATSAPP")` to check per-shop feature access before processing any bookings for that shop.

---

## Message Sending Limit System

Each shop has a configurable limit on the number of WhatsApp messages it can send within a given time period. This is enforced through the subscription system's feature usage tracking.

### How the Limit Is Calculated

The total message limit for a shop is calculated as:

**Total Limit = Base Limit (from plan) + Extra Limit (shop-specific override)**

- **Base Limit:** Defined in the `FeatureUsagePolicy` table for the shop's subscription plan and the `WHATSAPP_MESSAGE` feature. For example, a "Premium" plan might allow 5000 messages per month.
- **Extra Limit:** Defined in the `ShopFeatureLimit` table. This allows the platform admin to grant additional messages to a specific shop (e.g., +2000 extra messages for a high-volume shop). The extra limit can have an expiry date (`valid_until`) — if it has expired, it is ignored.

### Reset Cycles

The usage counter resets based on the `reset_cycle` configured in the `FeatureUsagePolicy`:

| Reset Cycle | Period Start | Period End |
|-------------|-------------|------------|
| `MONTHLY` | 1st of the current month | 1st of the next month |
| `DAILY` | Start of today (midnight) | Start of tomorrow (midnight) |
| `LIFETIME` | Subscription start date | Subscription end date |

### How Usage Is Tracked and Enforced

The `check_and_update_whatsapp_usage()` function is called **before every single message send**. It uses database-level locks to ensure atomicity:

- **Step 1:** Acquires a `select_for_update()` lock on the `Feature` record for `WHATSAPP_MESSAGE`
- **Step 2:** Verifies the shop has an active subscription
- **Step 3:** Looks up the `FeatureUsagePolicy` for the shop's plan to get the base limit and reset cycle
- **Step 4:** Checks for any `ShopFeatureLimit` override (extra messages) and verifies it hasn't expired
- **Step 5:** Calculates the current period (start/end) based on the reset cycle
- **Step 6:** Gets or creates a `ShopFeatureUsage` record for the current period using `select_for_update()`
- **Step 7:** Compares `usage.used` against `total_limit`. If the limit is reached, returns `(False, "limit exceeded message")`
- **Step 8:** If under the limit, increments `usage.used` by 1 and saves
- **Step 9:** Returns `(True, None)` indicating the message can be sent

This entire operation runs inside a `transaction.atomic()` block, ensuring that even under concurrent access, the usage counter is incremented correctly and no two tasks can both "claim" the last available message slot.

### Usage Rollback on Failure

The usage counter is incremented **before** the actual WhatsApp API call. If the message then fails to send (e.g., WACTO returns an error, or the client phone is invalid), the system **rolls back** the counter:

- It re-queries the `ShopFeatureUsage` record with `select_for_update()`
- It decrements `usage.used` by 1 (using `max(0, usage.used - 1)` to prevent negative values)
- This rollback runs inside its own `transaction.atomic()` block

This rollback mechanism ensures that failed messages do not consume the shop's message quota. The rollback is applied in the pickup and return reminder tasks. For invoice tasks, if the message fails, the task may retry up to 3 times before giving up.

---

## Booking Confirmation Message (Invoice)

### What This Message Does

When a new booking is created and the shop chooses to send a WhatsApp confirmation, the system generates a professional PDF invoice and sends it to the customer's WhatsApp number as a document attachment.

### Task: `send_booking_invoice_task`

- **Task type:** Celery `shared_task` with `bind=True` and `max_retries=3`
- **Triggered by:** The booking creation API, which calls `send_booking_invoice_task.delay(booking_id)`
- **Retry behavior:** On transient failures (network errors, S3 upload failures), the task retries up to 3 times with a 60-second delay. On validation errors (invalid phone, missing data), the task does **not** retry.

### Step-by-Step Logic

**Step 1 — Fetch the booking with all related data.**

- The task fetches the `BookingV2` record using `select_related` for `shop`, `client_identity`, and `staff`, and `prefetch_related` for `booked_items` and `additional_charges`.
- This single optimized query loads all the data needed for PDF generation in one database hit.

**Step 2 — Validate the client phone number.**

- The customer phone is read from `client.phone_1_e164` (the E.164 formatted phone stored on the client record).
- If the client has no phone number, the task raises a `ValueError` and does **not** retry — there is no point retrying if the phone number is missing.
- The phone number is validated and formatted using `validate_and_format_phone()`.

**Step 3 — Validate the shop name and shop phone number.**

- The shop must have both a `name` and a `phone` field. If either is missing, the task raises a `ValueError`.
- The shop phone is validated and formatted the same way as the client phone.

**Step 4 — Get the customer name.**

- The customer name is read from `client.client_name`. If it is empty or missing, the fallback value `"Customer"` is used.

**Step 5 — Check WhatsApp usage limit.**

- The `check_and_update_whatsapp_usage(shop)` function is called.
- If the shop has exceeded its message limit, the task logs a warning and returns `False` without raising an error. This means the message is silently skipped — no retry is triggered.

**Step 6 — Generate the PDF invoice.**

- The `BookingPDFService.generate_transaction_pdf()` method is called with the shop, client, booking, and booked items.
- This method uses **WeasyPrint** to render an HTML template into a PDF with:
  - Shop logo and branding
  - Customer details
  - Booking ID, pickup date, return date
  - List of booked items with prices
  - Total amount, discount, advance, and balance due
  - Terms and conditions
  - Full Malayalam font support for regional text
- The method returns the PDF as a **base64-encoded string**.

**Step 7 — Validate the generated PDF.**

- The base64 string is decoded using `base64.b64decode()` with `validate=True` to ensure it is valid base64. If it is corrupted, a `ValueError` is raised.

**Step 8 — Upload the PDF to AWS S3.**

- The PDF is uploaded to S3 under the path `whatsapp-invoices/{YYYY}/{MM}/{DD}/{unique_id}_{filename}.pdf`.
- A unique 8-character hex ID (from `uuid4`) is prepended to the filename to prevent collisions.
- The upload uses boto3's `put_object` with `ContentType='application/pdf'`.
- The returned URL is a public S3 URL in the format `https://{bucket}.s3.{region}.amazonaws.com/{key}`.
- WACTO requires publicly accessible URLs — this is why the PDF must be uploaded to S3 rather than sent as base64.

**Step 9 — Send the WhatsApp message via WACTO.**

- The `WhatsAppService.send_booking_invoice_template()` method is called with:
  - `phone_number` — the customer's phone in E.164 format
  - `customer_name` — the customer's name
  - `shop_name` — the shop's name
  - `shop_phone` — the shop's phone number
  - `pdf_url` — the public S3 URL
  - `filename` — the PDF filename (e.g., `Booking_000123.pdf`)
- The template `rental_invoice_5` is used, which includes a document header, body text with customer/shop details, and a "Call Shop" button.

**Step 10 — Handle the response.**

- If the WACTO API returns success, the task logs a success message and returns `True`.
- If the API returns an error, the task logs the full request/response details and retries (unless it is a validation error like "replace" or "undefined" in the error text).

---

## Sales Confirmation Message (Invoice)

### What This Message Does

When a sale is created and the shop chooses to send a WhatsApp confirmation, the system generates a PDF sales invoice and sends it to the customer via WhatsApp.

### Task: `send_sale_invoice_task`

- **Task type:** Celery `shared_task` with `bind=True` and `max_retries=3`
- **Triggered by:** The sale creation API, which calls `send_sale_invoice_task.delay(sale_id)`
- **Retry behavior:** Same as the booking invoice task — retries on transient errors, no retry on validation errors.

### Step-by-Step Logic

The logic is nearly identical to the booking invoice task, with these differences:

- **Data source:** Fetches the `Sale` record with `select_related("shop", "client", "staff")` and `prefetch_related("items", "payments")`
- **Client phone:** Read from `sale.client_phone_e164` instead of `client.phone_1_e164`
- **PDF generation:** Uses `SalesPDFService.generate_sale_pdf()` instead of `BookingPDFService.generate_transaction_pdf()`
- **Template:** Uses `sale_invoice_2` instead of `rental_invoice_5`
- **Filename:** Format is `Sale_{shop_sale_id}.pdf` instead of `Booking_{shop_booking_id}.pdf`
- **WhatsApp method:** Uses `send_sale_invoice_template()` instead of `send_booking_invoice_template()`

The template `sale_invoice_2` has the same structure as the booking template — document header, body with customer/shop name, and a "Call Shop" button — but the body parameters are slightly different (no shop phone in body, only customer name and shop name).

---

## Pickup Reminder

### What This Message Does

The pickup reminder is sent to customers who have a booking scheduled for pickup on the current day. It is a text-only message (no PDF attachment) that informs the customer to collect their rented product from the shop.

### Task: `send_pickup_ready_notifications`

- **Task type:** Celery `shared_task` with `bind=True` (no max_retries — runs once per schedule)
- **Triggered by:** Celery Beat at the configured daily schedule time

### Step-by-Step Logic

**Step 1 — Check the global kill switch.**

- The task first checks `settings.WHATSAPP_REMINDERS_ENABLED`. If this is `False`, the task logs a message and returns `{"sent": 0, "failed": 0, "disabled": True}` immediately.
- This allows the platform administrator to globally disable all reminder messages with a single setting change.

**Step 2 — Fetch today's pickup bookings.**

- The task queries all `BookingV2` records where:
  - `pickup_date__date` equals today
  - `delivery_status` is one of `'booked'` or `'ready to deliver'` (products not yet dispatched)
  - `pickup_date__gt=now` — the pickup time must still be in the future (no point reminding about a pickup that has already passed)
- The query uses `select_related` for `shop`, `shop__subscription`, and `client_identity` to minimize database hits.
- The query uses `.only()` to fetch only the specific fields needed, reducing memory usage.

**Step 3 — Filter and group bookings by shop.**

- Each booking is checked for eligibility:
  - Must have a `client_identity` with a valid `phone_1_e164`
  - **Same-day handling:** If the booking has both pickup and return on the same day and the pickup time has already passed, the pickup reminder is **skipped**. The return reminder task will handle this booking instead.
  - The shop must have an active subscription
- Eligible bookings are grouped by `shop_id` into a dictionary for batch processing.

**Step 4 — Check per-shop feature access.**

- For each shop, the task checks whether any active `ShopUser` in that shop has the `BOOKING_REMINDERS_WHATSAPP` feature enabled.
- This is done by iterating over the shop's active users and calling `has_feature_access(shop_user, "BOOKING_REMINDERS_WHATSAPP")`.
- If no user in the shop has this feature, all bookings for that shop are skipped.

**Step 5 — Validate the shop phone number.**

- The shop's phone number is validated using `validate_and_format_phone()`.
- If the shop phone is invalid, all bookings for that shop are skipped.

**Step 6 — Send notifications for each booking.**

- For each booking in the shop:
  - **Check usage limit:** `check_and_update_whatsapp_usage(shop)` is called. If the limit is exceeded, the booking is skipped and counted as failed.
  - **Validate client phone:** The client's phone is validated. If invalid, the usage counter is **rolled back** (since it was incremented in the previous step but no message was actually sent), and the booking is counted as failed.
  - **Send the message:** `WhatsAppService.send_pickup_ready_template()` is called with the customer name, shop name, client phone, and shop phone.
  - **Handle response:** If WACTO returns success, the booking is counted as sent. If WACTO returns an error, the usage counter is **rolled back** and the booking is counted as failed.

**Step 7 — Return summary.**

- The task returns `{"sent": total_sent, "failed": total_failed}`.

---

## Return Reminder

### What This Message Does

The return reminder is sent to customers who have a booking with a return date on the current day. It is a text-only message reminding the customer to return the rented product to the shop.

### Task: `send_return_reminder_notifications`

- **Task type:** Celery `shared_task` with `bind=True` (no max_retries)
- **Triggered by:** Celery Beat at the configured daily schedule time

### Step-by-Step Logic

The logic is nearly identical to the pickup reminder task, with these key differences:

**Booking query:**

- Filters on `return_date__date=today` instead of `pickup_date__date`
- Filters `delivery_status` as `'booked'`, `'ready to deliver'`, **or** `'delivered'` — the "delivered" status is included because some shops forget to update the delivery status, and the customer still needs to be reminded about the return
- Filters `return_date__gt=now` — the return time must still be in the future

**Same-day handling (opposite of pickup):**

- If the booking has both pickup and return on the same day:
  - If the pickup time is **still in the future** (`pickup_date > now`) → the return reminder is **skipped**. The pickup reminder task will handle this booking instead.
  - If the pickup time has **already passed** (`pickup_date <= now`) → the return reminder is sent.

This complementary logic between the pickup and return tasks ensures that for same-day bookings:

- If the pickup time has not yet passed → only the pickup reminder is sent (by the pickup task)
- If the pickup time has already passed → only the return reminder is sent (by the return task)
- The customer never receives both reminders on the same day for the same booking

**Template:** Uses `return_reminder_1` instead of `pickup_reminder_2`.

**WhatsApp method:** Uses `send_return_reminder_template()` instead of `send_pickup_ready_template()`.

Everything else — feature access checks, usage limit enforcement, phone validation, usage rollback — is identical to the pickup reminder task.

---

## Avoiding Duplicate Messages

The system has multiple safeguards to prevent sending duplicate or redundant messages to the same customer:

### Same-Day Booking Logic

The most important deduplication logic handles bookings where the pickup date and return date fall on the same day:

- **Pickup task checks:** If the booking is same-day and the pickup time has already passed → skip (let the return task handle it)
- **Return task checks:** If the booking is same-day and the pickup time has **not** yet passed → skip (let the pickup task handle it)

This ensures exactly one reminder is sent per same-day booking:

| Scenario | Pickup Time Status | Pickup Reminder | Return Reminder |
|----------|--------------------|-----------------|-----------------|
| Same-day booking, pickup time in future | Not yet passed | **Sent** | Skipped |
| Same-day booking, pickup time in past | Already passed | Skipped | **Sent** |
| Different dates | N/A | **Sent** (on pickup day) | **Sent** (on return day) |

### Future-Time Filtering

Both tasks filter bookings where the relevant time (pickup or return) is still in the future (`__gt=now`). This prevents:

- Sending a pickup reminder for a booking whose pickup time was at 8 AM when the task runs at 10 AM
- Sending a return reminder for a booking that was supposed to be returned at 9 AM

### Delivery Status Filtering

- **Pickup reminders** are only sent for bookings with delivery status `'booked'` or `'ready to deliver'`. If the product is already `'delivered'`, there is no need to remind about pickup.
- **Return reminders** include `'booked'`, `'ready to deliver'`, and `'delivered'` statuses. The `'delivered'` status is included because these are the bookings where the customer actually has the product and needs to return it.

---

## PDF Invoice Generation

The PDF generation engine is built using **WeasyPrint**, a Python library that converts HTML/CSS to PDF. The implementation supports full **Malayalam language rendering** through custom font configuration.

### Architecture

The PDF generation is organized into three classes:

| Class | Purpose |
|-------|---------|
| `BasePDFService` | Base class with shared utilities — static file helpers, currency formatting, image downloading, font configuration, and the core `generate_pdf()` method |
| `BookingPDFService` | Generates booking/rental invoice PDFs with item details, booking dates, measurements, and totals |
| `SalesPDFService` | Generates sale invoice PDFs with item details, payment information, and totals |

### Key Features

- **Malayalam font support:** The system detects Malayalam characters in text and automatically switches to the appropriate font family. Fonts are loaded from the `staticfiles/fonts/` directory.
- **Image downloading with circuit breaker:** Shop logos and product images are downloaded from S3 URLs. A circuit breaker pattern prevents repeated requests to failing endpoints — after 3 consecutive failures for a URL, requests are blocked for 60 seconds.
- **Parallel image downloads:** Multiple images (e.g., shop logo + product images) are downloaded concurrently using a `ThreadPoolExecutor` with up to 5 workers.
- **S3 upload for WACTO compatibility:** The generated PDF is base64-encoded, then uploaded to S3 under the `whatsapp-invoices/` prefix with a date-based path structure (`YYYY/MM/DD/`).

### Security Configuration

The `ImageDownloadConfig` class controls image download behavior:

- **Timeout:** 10 seconds per image download (allows for cross-region S3 requests)
- **Max file size:** 10 MB per image
- **Circuit breaker:** Opens after 3 failures, recovers after 60 seconds, requires 2 successes to close
- **Rate limiting:** Maximum 10 requests per URL per minute

---

## WhatsApp Redirect URL

In addition to the messaging functionality, the module provides a simple redirect view for WhatsApp deep links.

- **Endpoint:** `GET /call/<number>/`
- **Logic:** Redirects the user's browser to `https://wa.me/{number}`, which opens the WhatsApp chat with that phone number.
- **Use case:** This URL can be embedded in invoices, emails, or other communications to let customers quickly start a WhatsApp conversation with the shop.

This is the only HTTP endpoint exposed by the WhatsApp module — all other functionality is task-based with no REST API.

---

## Relationship with Other Modules

### Booking Module

- The `send_booking_invoice_task` receives a `booking_id` and fetches the `BookingV2` record with all related data (shop, client, items, charges)
- The `send_pickup_ready_notifications` task queries `BookingV2` records with today's pickup dates
- The `send_return_reminder_notifications` task queries `BookingV2` records with today's return dates

### Sales Module

- The `send_sale_invoice_task` receives a `sale_id` and fetches the `Sale` record with all related data (shop, client, items, payments)

### Subscription Module

The subscription module provides the entire feature access and usage limit infrastructure:

| Model | Role in WhatsApp Module |
|-------|------------------------|
| `Feature` | Defines the `WHATSAPP_MESSAGE` and `BOOKING_REMINDERS_WHATSAPP` features |
| `FeatureUsagePolicy` | Sets the base message limit per plan (e.g., 5000/month) and the reset cycle (MONTHLY/DAILY/LIFETIME) |
| `ShopFeatureLimit` | Allows per-shop extra message limits with optional expiry dates |
| `ShopFeatureUsage` | Tracks actual message counts per shop per period — the counter that is incremented/rolled back |
| `has_feature_access()` | Used by reminder tasks to check if a shop has the reminder feature enabled |

### Services Module (S3)

- PDF invoices are uploaded to AWS S3 using `boto3` directly (not through the services module's upload utility)
- The S3 bucket must allow public read access for the `whatsapp-invoices/` prefix, because WACTO requires publicly accessible document URLs

---

## Error Handling and Retry Strategy

The module uses different retry strategies depending on the type of message:

### Invoice Tasks (Booking and Sale)

- **Max retries:** 3
- **Retry delay:** 60 seconds between retries
- **Retries on:** Network errors, S3 upload failures, WACTO API errors
- **Does NOT retry on:** Missing phone number, invalid phone format, missing shop name, corrupted PDF, usage limit exceeded

### Reminder Tasks (Pickup and Return)

- **No retries per booking.** If sending fails for one booking, the task moves on to the next booking.
- **Usage rollback:** If a message fails to send after the usage counter was already incremented, the counter is rolled back inside a separate `transaction.atomic()` block.
- **Continues on failure:** A single failed booking does not stop the entire task — all remaining bookings are still processed.

---

## Summary

The **WhatsApp Integration Module** enables automated customer communication through WhatsApp using the WACTO Live service.

Key capabilities include:

- **Booking invoice delivery** — generates a professional PDF invoice using WeasyPrint, uploads it to S3, and sends it as a WhatsApp document attachment using the `rental_invoice_5` template
- **Sales invoice delivery** — same flow as booking invoices but using the `sale_invoice_2` template and sale-specific data
- **Pickup reminders** — daily background task that sends text reminders to customers with bookings scheduled for pickup today, using the `pickup_reminder_2` template
- **Return reminders** — daily background task that sends text reminders to customers with bookings due for return today, using the `return_reminder_1` template
- **Background processing** — all messages are sent via Celery tasks to keep the main API responsive, with automatic retries for transient failures
- **Template-based messaging** — all messages use pre-approved WACTO templates with dynamic parameters for customer name, shop name, phone numbers, and document URLs
- **Phone number validation** — rigorous E.164 validation using the `phonenumbers` library, with automatic formatting for Indian phone numbers
- **Subscription-based access control** — the `WHATSAPP_MESSAGE` feature controls invoice messages, and `BOOKING_REMINDERS_WHATSAPP` controls reminder messages
- **Message limit management** — atomic counter tracking with database-level locks, configurable reset cycles (monthly/daily/lifetime), and base + extra limit calculation per shop
- **Usage rollback** — failed messages do not consume the shop's message quota; the counter is rolled back on failure
- **Same-day deduplication** — complementary logic between pickup and return tasks ensures only one reminder is sent per same-day booking
- **PDF generation with Malayalam support** — WeasyPrint-based PDF engine with custom fonts, parallel image downloads, and circuit breaker protection for external image URLs

This module improves customer engagement and helps shops manage bookings and sales communication efficiently through automated, reliable WhatsApp messaging.
