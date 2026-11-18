# PDF Service Module

---

## Overview

The **PDF Service Module** is responsible for generating downloadable PDF reports for different financial and operational data in the system. Many parts of the application require the ability to export reports as PDF documents — ledger books, transaction summaries, expense breakdowns, pending payment lists, and security deposit records.

Instead of generating PDFs synchronously during API requests (which could take several seconds for large reports), the system uses **asynchronous background processing** through Celery workers. This ensures that the main API remains fast and responsive even when generating large reports with hundreds of transactions.

The module provides a complete pipeline for:

- Accepting PDF generation requests from the Flutter frontend
- Creating a trackable task record in the database
- Running PDF generation in a background Celery worker
- Fetching and aggregating transaction data from multiple modules (bookings, sales, expenses, payments, pendings, security deposits, tailor orders)
- Generating a professionally styled PDF using **ReportLab** with custom colors, shop branding, and dynamic table layouts
- Uploading the generated PDF to **AWS S3 storage**
- Returning a temporary download URL that expires after 24 hours
- Allowing the frontend to poll for task status with progress estimation

The module connects directly with:

- **Booking module** — booking records, booked items, payment details, refund details, security deposit adjustments and payments
- **Sales module** — sale records, sale items, sales payment details
- **Expenses module** — general expenses and product variant expenses
- **Tailor module** — tailor orders, customer payments, tailor order items
- **Shop module** — shop information for PDF headers (name, address, phone, logo)
- **S3 service** — for uploading generated PDFs and generating temporary download URLs

---

## Supported PDF Types

The system currently supports three types of PDF reports:

| PDF Type | Internal Code | Description |
|----------|--------------|-------------|
| Product Report | `product_report` | Products and inventory information |
| Ledger Book | `ledger_book` | Complete financial ledger with shop branding, summary section, expense breakdowns, and detailed transaction tables — generated using **ReportLab** with professional styling |
| Transaction Report | `transaction_report` | Basic line-by-line transaction listing — generated using **ReportLab** with simple formatting |

Each of these report types can include data from multiple transaction categories:

- **Bookings** — all rental bookings created within the date range, showing product names, paid/pending amounts, and totals
- **Sales** — all sales (regular + completed tailor order sales) within the date range, showing product names, payment method, and amounts
- **Payments** — all payment receipts (booking payments, sales payments, tailor payments, cancelled booking penalties, and security deposit deductions) within the date range
- **Pendings** — all unpaid balances (booking pendings, sales pendings, and tailor order pendings) within the date range
- **Expenses** — all general expenses and product variant expenses within the date range
- **Security Deposits** — all bookings and tailor orders with security deposits, showing amounts collected, refunded, deducted, and still pending

The user can request a single transaction type (e.g., only "sales") or multiple types (e.g., "bookings,sales,payments") or all types at once ("all"). The PDF layout, table columns, and summary calculations automatically adapt based on which transaction types are included.

---

## PDF Generation Architecture

The PDF generation process is designed as a **fully asynchronous pipeline**. The frontend never waits for the PDF to be generated — it receives a task ID immediately and polls a separate status endpoint until the PDF is ready.

The complete architecture flow is:

- **Step 1:** The Flutter frontend sends a `GET` request to the PDF generation endpoint with query parameters (date range, transaction types, optional client ID)
- **Step 2:** The backend validates the parameters (date format, date range, max 365 days)
- **Step 3:** The backend checks for **duplicate requests** — if an identical request (same parameters, same shop) was made within the last 15 minutes and is still pending or processing, the existing task ID is returned instead of creating a new one
- **Step 4:** The backend fetches all transactions matching the parameters to calculate an **estimated record count** and **estimated generation time** (approximately 80-100 transactions per second)
- **Step 5:** A `PDFTask` record is created in the database with status `"pending"`
- **Step 6:** A Celery background task is triggered (`generate_ledger_pdf_task.delay()` or `generate_transaction_pdf_task.delay()`)
- **Step 7:** The API immediately returns the task ID, status, estimated count, and estimated time to the frontend
- **Step 8:** The Celery worker picks up the task, marks it as `"processing"`, fetches the data, generates the PDF, uploads it to S3, and marks the task as `"completed"` with the download URL
- **Step 9:** The frontend polls the status endpoint, and once the status is `"completed"`, it gets the download URL and presents it to the user

This approach ensures:

- The API response is fast (typically under 2 seconds) regardless of report size
- Multiple PDF generation requests can be processed concurrently by different Celery workers
- The frontend has full visibility into the progress of the generation
- Duplicate requests within 15 minutes are automatically deduplicated to prevent wasting server resources

---

## PDFTask Model

The `PDFTask` model is the central tracking table for all PDF generation jobs. Every time a user requests a PDF report, a new record is created in this table. The record tracks the entire lifecycle of the PDF generation — from the initial request through processing to completion or failure.

### Task Identification

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `CharField(max_length=255, unique=True)` | A UUID that uniquely identifies the PDF generation task. This is the value returned to the frontend and used to poll for status. It has a `db_index` for fast lookups. |
| `pdf_type` | `CharField(max_length=50, choices)` | The type of PDF being generated. Currently supports `product_report`, `ledger_book`, and `transaction_report`. Has a `db_index` for filtering by type. |

### Relationships

| Field | Type | Description |
|-------|------|-------------|
| `shop` | `ForeignKey('shop.Shop', CASCADE)` | The shop that requested the report. When the shop is deleted, all its PDF tasks are also deleted. Uses `related_name='pdf_service_tasks'`. |
| `user` | `ForeignKey('authentication.User', CASCADE)` | The authenticated user who initiated the request. Used for audit tracking and rate limiting. Uses `related_name='pdf_service_tasks'`. |

### PDF Storage

| Field | Type | Description |
|-------|------|-------------|
| `s3_key` | `CharField(max_length=500, nullable)` | The S3 object key where the generated PDF is stored. Format: `pdf_reports/shop_{id}/ledger/{task_id}_{timestamp}.pdf` or `pdf_reports/shop_{id}/transactions/{task_id}_{timestamp}.pdf`. Only populated after successful generation. |
| `download_url` | `URLField(max_length=1000, nullable)` | The temporary S3 download URL. This URL includes the `Content-Disposition` header so the browser downloads the file with a user-friendly filename. Only populated after successful generation. |
| `url_expires_at` | `DateTimeField(nullable)` | The expiration time of the download URL. Set to **24 hours after generation**. After this time, the status endpoint will report `url_expired: true` and prompt the user to regenerate. |

### Status Tracking

| Field | Type | Description |
|-------|------|-------------|
| `status` | `CharField(max_length=20, choices, default='pending')` | The current state of the task. See the Task Status Lifecycle section below for details. Has a `db_index` for filtering. |
| `error_message` | `TextField(nullable)` | If the task fails, the error message is stored here. Truncated to 1000 characters to prevent storage bloat from large stack traces. |

### Request Details

| Field | Type | Description |
|-------|------|-------------|
| `request_params` | `JSONField(default=dict)` | The complete set of parameters used to generate the report — `from_date`, `to_date`, `client_id`, `txn_types`. Stored as JSON so the Celery worker can reconstruct the exact same query. |
| `request_hash` | `CharField(max_length=64, nullable)` | A SHA-256 hash of the normalized request parameters. Used for **request deduplication** — if a task with the same hash already exists and is still in progress within the last 15 minutes, the existing task is returned instead of creating a new one. Has a `db_index` for fast lookups. |

### Performance Metadata

| Field | Type | Description |
|-------|------|-------------|
| `estimated_count` | `IntegerField(nullable)` | The estimated number of transactions in the report. Calculated before generation begins by running the query and counting results. Displayed to the user as a progress indicator. Updated again by the Celery worker when actual data is fetched. |
| `estimated_time` | `CharField(max_length=50, nullable)` | A human-readable estimate of how long the generation will take (e.g., `"5s"`, `"12s"`). Calculated as approximately 80-100 transactions per second. |
| `actual_generation_time` | `FloatField(nullable)` | The actual wall-clock time in seconds that the Celery worker took to generate the PDF, from start to S3 upload completion. |
| `file_size_bytes` | `BigIntegerField(nullable)` | The size of the generated PDF file in bytes. Used for logging and monitoring. |

### Timestamps

| Field | Type | Description |
|-------|------|-------------|
| `created_at` | `DateTimeField(auto_now_add=True)` | When the task was first created. Has a `db_index` for ordering and cleanup queries. |
| `started_at` | `DateTimeField(nullable)` | When the Celery worker began processing the task. Set by the `mark_processing()` method. |
| `completed_at` | `DateTimeField(nullable)` | When the task finished — either successfully or with a failure. Set by both `mark_completed()` and `mark_failed()`. |

### Model Methods

The `PDFTask` model provides three helper methods that encapsulate status transitions. These methods use `update_fields` to only save the specific fields being changed, which is more efficient than saving the entire model:

- **`mark_processing()`** — Sets `status` to `'processing'` and records `started_at`. Called by the Celery worker when it begins processing the task.
- **`mark_completed(s3_key, download_url, generation_time, file_size)`** — Sets `status` to `'completed'`, records the S3 key, download URL, URL expiration (24 hours from now), actual generation time, file size, and `completed_at` timestamp.
- **`mark_failed(error_message)`** — Sets `status` to `'failed'`, stores the error message (truncated to 1000 characters), and records `completed_at` timestamp.

### Database Configuration

- **Table name:** `pdf_service_task`
- **Default ordering:** `-created_at` (newest first)
- **Composite indexes:**
  - `(shop, pdf_type, status, -created_at)` — for quickly finding a shop's latest tasks of a specific type and status
  - `(request_hash, -created_at)` — for request deduplication lookups

---

## Task Status Lifecycle

A PDF task moves through a well-defined sequence of states:

| Status | Meaning | Set By |
|--------|---------|--------|
| `pending` | Task created in the database, waiting for a Celery worker to pick it up | API view during task creation |
| `processing` | Celery worker has started working — fetching data, generating PDF, or uploading to S3 | Celery worker via `mark_processing()` |
| `completed` | PDF generated successfully, download URL available | Celery worker via `mark_completed()` |
| `failed` | PDF generation failed due to an error (missing data, query failure, ReportLab error, S3 upload failure) | Celery worker via `mark_failed()` |
| `expired` | Download URL has passed its 24-hour expiration window | Detected by the status API at query time |

The normal successful flow is: `pending → processing → completed`

If something goes wrong: `pending → processing → failed`

The `expired` status is not explicitly set on the model — instead, the `PDFStatusView` checks `url_expires_at` at query time and reports the URL as expired if the current time is past the expiration.

---

## PDF APIs

The PDF service exposes four API endpoints, all under the `/api/v3/pdf/` prefix:

| Endpoint | Method | View Class | Purpose |
|----------|--------|------------|---------|
| `/api/v3/pdf/transactions/export/pdf/` | `GET` | `ExportTransactionsPDFView` | Generate a transaction report PDF |
| `/api/v3/pdf/ledger/export/pdf/` | `GET` | `ExportLedgerPDFView` | Generate a ledger book PDF |
| `/api/v3/pdf/status/<task_id>/` | `GET` | `PDFStatusView` | Check PDF generation status by URL path |
| `/api/v3/pdf/status/` | `GET` | `PDFStatusView` | Check PDF generation status by query parameter |

All endpoints require **JWT authentication** (`CutoffJWTAuthentication`) and the user must be authenticated (`IsAuthenticated`).

---

### 1. Transaction Report PDF API — `ExportTransactionsPDFView`

This endpoint generates a transaction report PDF. It is a `GET` request because the parameters are passed as query parameters rather than in a request body.

**Query Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `from_date` | Yes | Start date of the report period. Accepts `YYYY-MM-DD` or `DD-MM-YYYY` format. |
| `to_date` | Yes | End date of the report period. Accepts `YYYY-MM-DD` or `DD-MM-YYYY` format. |
| `type` or `txn_types` | No | Comma-separated list of transaction types to include. Options: `bookings`, `sales`, `payments`, `pendings`, `expenses`, `security`. Defaults to `all` if not provided. |
| `client_id` | No | Filter transactions for a specific client only. The system verifies the client belongs to the requesting shop before proceeding. |

**Step-by-step logic in the view:**

- **Step 1 — Get the shop ID** from the authenticated request using `get_active_shop_id(request)`.
- **Step 2 — Parse and validate dates.** The system accepts both `YYYY-MM-DD` and `DD-MM-YYYY` formats. If either date is missing or invalid, a validation error is returned. If `from_date > to_date`, a validation error is returned. If the date range exceeds 365 days, a validation error is returned to prevent out-of-memory errors on very large reports.
- **Step 3 — Parse transaction types.** The system supports both comma-separated format (`type=bookings,sales`) and repeated query params (`txn_types=bookings&txn_types=sales`). All type values are lowercased and trimmed. If nothing is provided, it defaults to `['all']`.
- **Step 4 — Compute request hash** using `compute_request_hash()`, which creates a SHA-256 hash of the normalized request parameters (JSON-serialized with sorted keys).
- **Step 5 — Check for duplicate requests.** The system queries `PDFTask` for any task with the same shop, same PDF type (`transaction_report`), same request hash, status `pending` or `processing`, and created within the last 15 minutes. If found, it returns the existing task ID with `reused: true` in the response — no new task is created.
- **Step 6 — Estimate the record count.** The system runs the actual transaction query (the same `get_transactions()` method used by the Celery worker) to count how many records will be included. This count is used for progress estimation.
- **Step 7 — Estimate generation time** as approximately `estimated_count / 100` seconds (minimum 1 second).
- **Step 8 — Create the PDFTask record** with a new UUID `task_id`, status `pending`, the request parameters, the request hash, and the estimated count/time.
- **Step 9 — Trigger the Celery task** with `generate_transaction_pdf_task.delay(task_id, shop_id, request_params)`.
- **Step 10 — Return the response** with the task ID, status, estimated transactions, estimated time, and the date period string.

**Example response:**

```json
{
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "pending",
    "estimated_transactions": 245,
    "estimated_time": "2s",
    "period": "01-01-2026 to 31-01-2026"
}
```

---

### 2. Ledger Book PDF API — `ExportLedgerPDFView`

This endpoint generates a professionally styled ledger book PDF. The query parameters, validation logic, duplicate detection, and response format are **identical** to the transaction report endpoint above.

The only differences are:

- The `pdf_type` is set to `'ledger_book'` instead of `'transaction_report'`
- The Celery task triggered is `generate_ledger_pdf_task` instead of `generate_transaction_pdf_task`
- The estimated generation time uses a slightly slower rate of `estimated_count / 80` (ledger PDFs are more complex due to ReportLab styling)
- The generated PDF has professional styling with shop branding, purple color scheme, summary tables, expense breakdowns, and type-specific table columns

---

### 3. PDF Status API — `PDFStatusView`

This endpoint allows the frontend to poll for the status of a PDF generation task. The task ID can be provided either as a URL path parameter (`/status/<task_id>/`) or as a query parameter (`/status/?task_id=...`).

**Step-by-step logic in the view:**

- **Step 1 — Get the task ID** from the URL path or query parameter. If neither is provided, return a validation error.
- **Step 2 — Look up the PDFTask** by `task_id`. If not found, return a 404 error.
- **Step 3 — Calculate elapsed time** since the task was created (in seconds).
- **Step 4 — Build the response** based on the current task status:

**If status is `completed`:**

- Check if the download URL has expired by comparing `url_expires_at` with the current time
- If not expired: return the `download_url`, `expires_at`, `generation_time`, `file_size_kb`, and `record_count`
- If expired: return `download_url: null`, `url_expired: true`, and a message telling the user to regenerate

**If status is `pending` or `processing`:**

- Return a progress message based on elapsed time:
  - Less than 10 seconds → `"Initializing..."`
  - 10-30 seconds → `"Processing data..."`
  - 30-60 seconds → `"Generating PDF..."`
  - More than 60 seconds → `"Taking longer than expected..."`
- Include the `estimated_time` from the task record

**If status is `failed`:**

- Return the `error_message` stored on the task

**Example completed response:**

```json
{
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "pdf_type": "ledger_book",
    "status": "completed",
    "elapsed_seconds": 8,
    "created_at": "2026-03-13T10:30:00.000000+05:30",
    "download_url": "https://s3-bucket-url/pdf_reports/shop_1/ledger/task_123.pdf",
    "expires_at": "2026-03-14T10:30:08.000000+05:30",
    "url_expired": false,
    "generation_time": "7.42s",
    "file_size_kb": 156.8,
    "record_count": 245
}
```

---

## Rate Limiting

The PDF service implements two separate rate limits to prevent abuse:

| Throttle Class | Rate | Applies To | Purpose |
|---------------|------|------------|---------|
| `PDFGenerationThrottle` | 20 requests per hour per user | Transaction export and ledger export endpoints | Prevents users from flooding the system with PDF generation requests. Each generation consumes CPU time, memory, and S3 storage. |
| `PDFStatusCheckThrottle` | 60 requests per minute per user | Status check endpoint | More permissive because status checks are lightweight database lookups. The frontend polls this endpoint every few seconds while waiting for a PDF to complete. |

Both throttle classes extend Django REST Framework's `UserRateThrottle`, which tracks usage per authenticated user.

---

## Request Deduplication

Before creating a new PDF generation task, both the transaction and ledger endpoints check for **duplicate requests**. This prevents the same user from accidentally triggering multiple identical PDF generations (e.g., by tapping the "Export" button multiple times).

The deduplication works as follows:

- **Step 1:** The request parameters (`from_date`, `to_date`, `client_id`, `txn_types`) are normalized into a JSON string with sorted keys.
- **Step 2:** A SHA-256 hash of this JSON string is computed using the `compute_request_hash()` function.
- **Step 3:** The system queries the `PDFTask` table for any task with:
  - The same `shop_id`
  - The same `pdf_type`
  - The same `request_hash`
  - Status `pending` or `processing` (not yet completed)
  - Created within the last 15 minutes
- **Step 4:** If a matching task is found, the existing task ID is returned with `reused: true` — no new Celery task is created.

This 15-minute deduplication window ensures that:

- Rapid double-taps on the export button are handled gracefully
- After 15 minutes, the user can request the same report again (useful if the data has changed)

---

## Transaction Data Aggregation — `ShopLedgerTransactionExportMixin`

The `ShopLedgerTransactionExportMixin` is a mixin class that contains all the logic for fetching and aggregating transaction data from multiple Django models. This mixin is used by both the API views (for estimating record counts) and the Celery tasks (for fetching the actual data that goes into the PDF).

The mixin provides two key methods:

### `parse_date(date_str)`

Parses a date string into a Python `date` object. Accepts two formats:

- `YYYY-MM-DD` (ISO format)
- `DD-MM-YYYY` (Indian format)

Returns `None` if the input is invalid or missing.

### `get_transaction_data(request, txn_types=None)`

Entry point when called from an API view. Extracts the shop ID, date range, transaction types, and optional client ID from the request's query parameters. Validates:

- Both dates must be provided
- `from_date` must not be after `to_date`
- If a `client_id` is specified, verifies the client belongs to the requesting shop

### `get_transactions(shop_id, from_date, to_date, txn_types, client_id=None)`

The core data fetching method. This is a large method that queries **seven different data sources** and combines them into a unified list of transaction dictionaries. Each transaction dictionary contains standardized fields that the PDF generator can use.

The method handles each transaction type with optimized database queries:

---

#### Bookings Section

- Queries `BookingV2` records created within the date range, excluding cancelled bookings and soft-deleted records
- Uses `select_related` for `client_identity` and `client` to avoid N+1 queries
- Uses a **Subquery with `StringAgg`** to aggregate all booked product names into a single comma-separated string (instead of making a separate query per booking)
- Calculates `effective_amount` (total minus discount) and `pending_amount` (total minus discount minus advance) at the database level using `F()` expressions and `Coalesce`
- If a `client_id` is provided, filters by `client_identity_id`
- Each booking is mapped to a dictionary with: `id`, `shop_booking_id`, `date`, `sort_date`, `type='bookings'`, `name` (client name, defaults to `'Walk-in'`), `client_phone`, `amount`, `product_names`, `paid_amount`, `pending_amount`

---

#### Pendings Section

The pendings section aggregates unpaid balances from **three separate sources**:

**Booking Pendings:**

- Queries `BookingV2` where `return_date` is within the date range, `payment_status=False` (not fully paid), not cancelled, not deleted
- Calculates `pending_amt` as `total_amount - discount_amount - advance_amount` at the database level
- Filters to only include records where `pending_amt > 0`
- Fetches booked product names in a separate batch query (using `BookedProductVariant.objects.filter(booking_id__in=booking_ids)`) and maps them by booking ID to avoid N+1 queries
- Truncates long product name strings to 50 characters with `...`

**Sales Pendings:**

- Queries `Sale` records where `created_at` is within the date range
- Calculates `total_paid` using `Sum('payments__payment_amount')` aggregate
- Calculates `pending_amt` as `paid_amount - total_paid`
- Filters to only include records where `pending_amt > 0`

**Tailor Order Pendings:**

- Queries `TailorOrder` records that are not deleted
- Calculates `pending_amt` as `customer_total_charge - paid_amount - discount_amount`
- Uses a `Case/When` expression for the date field: uses `pickup_date` for sale-type orders and `return_date` for rental-type orders
- Filters to only include records where `pending_amt > 0` and the relevant date falls within the date range
- Fetches tailor order items in a separate batch query and maps them by order ID

---

#### Payments Section

The payments section aggregates payment receipts from **five separate sources**:

**Booking Payments:**

- Queries `PaymentDetailsV2` where the booking's shop matches and the payment date is within the range
- Excludes cancelled bookings
- Normalizes payment method: `gpay` and `upi` both become `'UPI'`, everything else becomes `'Cash'`
- Calculates separate `upi` and `cash` amounts for the summary section

**Cancelled Booking Penalties:**

- Queries cancelled `BookingV2` records where the advance exceeds the total refunded amount
- Calculates `penalty_amount = advance_amount - total_refund`
- Uses the last refund date as the transaction date, falling back to the booking creation date if no refunds exist
- These are regular payments from the shop's perspective — the customer's advance that was retained as a cancellation penalty

**Security Deposit Deductions:**

- Queries `SecurityDepositAdjustment` records where `adjustment_type='deduction'`
- These represent security deposit amounts that were deducted against pending booking amounts — effectively a payment channel

**Sales Payments:**

- Queries `SalesPaymentDetails` where the sale's shop matches and the payment date is within the range
- Same UPI/Cash normalization as booking payments

**Tailor Order Payments:**

- Queries `CustomerPayment` where the order's shop matches and the payment date is within the range
- Same UPI/Cash normalization

---

#### Expenses Section

- Queries both `Expenses` (general expenses) and `ProductVariantExpense` (product-related expenses) within the date range
- Each expense record includes `expense_type` for category-level breakdown in the PDF summary

---

#### Sales Section

Aggregates sales from **two sources**:

**Regular Sales:**

- Queries `Sale` records within the date range
- Uses **Subquery with `StringAgg`** for product names (aggregated from `SaleItem → product_variant → product → name`)
- Uses a Subquery for the first payment method (ordered by `created_at`)
- Calculates `effective_amount = total_amount - discount`

**Completed Tailor Order Sales:**

- Queries `TailorOrder` where `customer_order_type='sale'`, `status='completed'`, and `completed_at` is within the range
- Uses **Subquery with `StringAgg`** for tailor order item names
- Same structure as regular sales

---

#### Security Section

Aggregates security deposits from **two sources**:

**Booking Security Deposits:**

- Queries `BookingV2` where `security_amount > 0`, not cancelled, not deleted
- Uses Subqueries to calculate:
  - `refunded_amount` — sum of `SecurityDepositAdjustment` where `adjustment_type='refund'`
  - `deducted_amount` — sum of `SecurityDepositAdjustment` where `adjustment_type='deduction'`
  - `pending_amount` — `security_amount - refunded - deducted`
- Special case: if a booking is completed but has **no adjustments and no payment records**, the full security amount is treated as refunded (assuming it was handled offline)
- Uses the latest `SecurityDepositPayment.payment_method` as the payment method

**Tailor Order Security Deposits:**

- Queries `TailorOrder` where `security_amount > 0`, not converted to a booking (`created_booking__isnull=True`), not deleted
- For tailor orders, the full security amount is treated as pending (no refund/deduction tracking exists yet)

---

#### Sorting Logic

After all transaction types are fetched and combined, the final list is sorted in two groups:

- **Non-pending transactions:** Sorted by date, **newest first** (descending)
- **Pending transactions:** Sorted by date, **oldest first** (ascending) — the oldest unpaid balance appears first, which is the most actionable for the shop

The final output is: non-pending transactions first, then pending transactions.

---

## Celery Background Tasks

The PDF service uses two Celery tasks for asynchronous PDF generation. Both tasks share the same structure but produce different output formats.

### Common Task Configuration

- **`bind=True`** — The task receives `self` as the first argument, enabling access to retry logic
- **`max_retries=1`** — Each task retries once on failure (total of 2 attempts)
- **`time_limit=300`** — Hard time limit of 5 minutes; the worker is killed if the task exceeds this
- **`soft_time_limit=280`** — Soft time limit of 4 minutes 40 seconds; raises `SoftTimeLimitExceeded` so the task can clean up gracefully

### `generate_ledger_pdf_task`

This task generates the professionally styled ledger book PDF using the `LedgerBookPDFService`.

**Step-by-step logic:**

- **Step 1 — Look up the PDFTask** record by `task_id`. If not found, raise a `ValueError`.
- **Step 2 — Mark as processing** using `pdf_task.mark_processing()`.
- **Step 3 — Get shop information** — fetches the `Shop` record and extracts `name`, `phone`, `phone2`, `email`, and `address` into a dictionary for the PDF header.
- **Step 4 — Fetch transaction data** — instantiates `ShopLedgerTransactionExportMixin`, parses the dates from the request parameters, and calls `get_transactions()` to fetch all matching transactions.
- **Step 5 — Update estimated count** — saves the actual transaction count to the PDFTask record (the initial estimate may differ slightly).
- **Step 6 — Generate the PDF** — calls `LedgerBookPDFService.generate_ledger_pdf()` with the shop info, transactions, date range, and shop object (for logo). This produces the PDF as raw bytes.
- **Step 7 — Upload to S3** — the PDF bytes are uploaded to `pdf_reports/shop_{id}/ledger/{task_id}_{timestamp}.pdf`. The `S3Service.upload_pdf()` method returns a temporary download URL with a 24-hour expiration. A user-friendly download filename is generated (e.g., `sales_01-01-2026_31-01-2026.pdf`) using the `build_pdf_download_filename()` helper.
- **Step 8 — Mark as completed** — records the S3 key, download URL, generation time, and file size.
- **Step 9 — Return result** — returns a dictionary with `task_id`, `status`, `download_url`, `generation_time`, `transaction_count`, and `file_size`.

If any step fails, the exception is caught, the error message is saved to the task via `mark_failed()`, and the exception is re-raised (allowing Celery to retry once).

### `generate_transaction_pdf_task`

This task generates a simpler transaction report using basic **ReportLab `canvas`** drawing (not the full `LedgerBookPDFService`).

The data fetching steps are identical to the ledger task. The difference is in Step 6:

- Creates a `BytesIO` buffer and a `reportlab.pdfgen.canvas.Canvas`
- Draws a "Shop Transaction Report" title and the date period at the top
- Iterates through all transactions, drawing each one as a single text line in the format:
  `[date] Type | Name | ₹Amount | Description | Items: ...`
- Long fields (name, description, items) are truncated to prevent overflow
- Automatically handles page breaks when the Y position falls below 50 points
- For sale-type transactions, includes the product items in the line

The upload and completion steps are identical to the ledger task, except the S3 key uses `transactions/` instead of `ledger/`.

### Download Filename Generation — `build_pdf_download_filename()`

This helper function generates a clean, user-friendly filename for the downloaded PDF:

- If all transaction types are included → filename starts with `"all"` (or `"ledger"` for ledger PDFs)
- If specific types are selected → sorted in canonical order (`sales`, `payments`, `expenses`, `bookings`, `pendings`, `security`) and joined with underscores
- The date range is appended in `DD-MM-YYYY` format
- The filename is sanitized to remove any characters that are invalid on Windows/macOS/Linux

**Examples:**

- `ledger_01-01-2026_31-01-2026.pdf`
- `sales_payments_01-01-2026_31-01-2026.pdf`
- `bookings_01-01-2026_31-01-2026.pdf`

---

## Ledger Book PDF Generation — `LedgerBookPDFService`

The `LedgerBookPDFService` class in `pdf_service/utils/ledger_report_pdf.py` generates professionally styled ledger book PDFs using the **ReportLab** library. This is a large and complex module (~1500 lines) that handles every aspect of the PDF layout.

### PDF Libraries Used

| Library | Purpose |
|---------|---------|
| **ReportLab** | Core PDF generation — `SimpleDocTemplate`, `Table`, `TableStyle`, `Paragraph`, `Image`, `Spacer`, `Canvas` |
| **Pillow (PIL)** | Image processing — resizing shop logos, EXIF rotation correction, format conversion |
| **svglib** (optional) | SVG to ReportLab drawing conversion for vector images |

### Color Scheme

The PDF uses a consistent purple-themed color scheme that matches the application branding:

| Color | Hex Code | Used For |
|-------|----------|----------|
| Purple | `#7C61A0` | Table headers, shop name, separator lines |
| White | `#FFFFFF` | Table header text, main row background |
| Light Purple | `#F7F3FE` | Alternating table row background |
| Light Pink | `#FEFBFE` | Summary table background |
| Light Purple Border | `#E5D8EF` | Summary table borders |
| Red | `#EF4444` | Negative amounts (where applicable) |
| Dark Grey | `#666666` | Contact details, addresses |
| Light Grey | `#F9FAFB` | Expense breakdown category rows |

### PDF Page Structure

Each generated ledger PDF has the following structure from top to bottom:

**1. Header Section**

- Shop logo (downloaded from S3, resized to 50×50 pixels with aspect ratio maintained)
- Shop name in purple bold text
- Shop address, phone numbers, and email in grey text
- A thin purple separator line beneath the header

**2. Title Section**

- "Ledger Book Statements" in large bold text
- Dynamic period text like "Sales and Bookings from 01-01-2026 - 31-01-2026"
- The period label is automatically generated based on which transaction types are included — if one type is selected, it shows that type name; if multiple, they are joined with "and" (with Oxford comma for 3+)

**3. Summary Section**

The summary content adapts based on which transaction types are present:

- **Payments only:** Shows "Total UPI", "Total Cash", "Grand Total"
- **Expenses only:** Shows "Total UPI", "Total Cash", "Grand Total"
- **Security only:** Shows "Total Amount", "Total Deducted", "Total Refunded", "Total Pending"
- **Payments + Expenses:** Shows "Total Received", "Total Expense", "Balance" (payments minus expenses)
- **Multiple types:** Shows per-type totals (Bookings, Sales, Expenses, etc.) plus a "Grand Total"

**4. Expense Breakdown Section** (only when expenses are present)

- Shows each expense category with its total amount
- Categories are extracted from the `expense_type` field on each expense transaction
- Sorted by total amount (highest first)
- Displayed in a table with light grey background for categories and light purple for the total row

**5. Transactions Table**

The table columns vary based on which transaction type is being displayed:

| Transaction Type | Columns |
|-----------------|---------|
| **Bookings** | Date, Product, Name, Paid, Pending, Total |
| **Sales** | Date, Product Name, Payment Method, Amount |
| **Expenses** | Date, Name, Payment Method, Amount |
| **Payments** | Date & Time, Customer Name, Payment Method, Amount |
| **Pendings** | Date, Customer Name, Product Name, Phone, Amount |
| **Security** | Date & Time, ID, Name, Amount, Deducted, Refunded |
| **Mixed types** | Date, Type, Customer/Category, Phone, Amount |

The table uses:

- Purple header row with white text
- Alternating white and light purple row backgrounds
- Horizontal grid lines only (no vertical lines for a cleaner look)
- Left and right borders for the entire table
- `repeatRows=1` to repeat the header row on every page

**6. Page Numbers**

- Page numbers are rendered at the bottom center of every page in `"Page X of Y"` format
- This is implemented using a custom `PageNumberCanvas` class that overrides ReportLab's canvas to perform a two-pass render: first to count total pages, then to draw the page numbers

### Image Handling and Security

The module includes secure image downloading for shop logos:

- **SSRF Prevention:** All image URLs are validated against a whitelist of allowed domains. The whitelist is built from the configured AWS S3 bucket domain, custom S3 domain, and any additional trusted CDN domains from `settings.ALLOWED_IMAGE_DOMAINS`. URLs from unlisted domains are blocked.
- **Size Limits:** Images are limited to 5 MB maximum. The download streams the content in 8 KB chunks and aborts if the accumulated size exceeds the limit, preventing memory exhaustion from malicious large files.
- **Timeout:** Image downloads have a 4-second timeout to prevent hanging on slow or unresponsive servers.
- **Retry:** One automatic retry on timeout or connection errors.
- **Image Resizing:** Downloaded images are resized to the target dimensions (default 60×60 for logos) using Pillow's `Image.LANCZOS` filter for high-quality downscaling. Aspect ratio is maintained by centering the image on a white canvas.
- **EXIF Handling:** Pillow's `exif_transpose()` is used to automatically rotate images based on their EXIF orientation data.
- **Caching:** Global dictionaries (`_IMAGE_CACHE`, `_STYLE_CACHE`, `_PLACEHOLDER_CACHE`, `_SVG_CACHE`) cache downloaded images, paragraph styles, placeholder drawings, and SVG renderings to avoid redundant processing within a single PDF generation.

### Helper Methods

| Method | Purpose |
|--------|---------|
| `normalize_payment_method(method)` | Normalizes payment method strings — anything containing `"cash"` becomes `"Cash"`, everything else becomes `"UPI"` |
| `format_date(date_str)` | Converts various date formats to `DD-MM-YYYY` display format |
| `format_currency(amount)` | Formats a number with the `₹` symbol and comma grouping (e.g., `₹12,500`) |
| `calculate_total(transactions)` | Sums the `amount` field across all transaction dictionaries |
| `get_shop_logo_safe(shop, size)` | Safely downloads and resizes the shop logo, returning a placeholder drawing if the download fails |
| `detect_transaction_types(transactions)` | Returns the set of unique `type` values from the transaction list |

### Font Configuration

The module attempts to register **Inter** font family (Regular and SemiBold) from the `staticfiles/fonts/` directory. If the font files are not found, it falls back to the built-in **Helvetica** and **Helvetica-Bold** fonts. Font registration happens once at module load time.

---

## Security and Storage

### S3 Upload

Generated PDF files are uploaded to AWS S3 with the following path structure:

- Ledger PDFs: `pdf_reports/shop_{shop_id}/ledger/{task_id}_{timestamp}.pdf`
- Transaction PDFs: `pdf_reports/shop_{shop_id}/transactions/{task_id}_{timestamp}.pdf`

The timestamp in the path uses `YYYYMMDD_HHMMSS` format to ensure uniqueness.

### Download URLs

The `S3Service.upload_pdf()` method generates a **presigned download URL** with:

- A 24-hour expiration configured via the `expires_in_hours=24` parameter
- A `Content-Disposition` header that sets a user-friendly download filename (e.g., `ledger_01-01-2026_31-01-2026.pdf`)

After 24 hours, the URL expires and the status API reports `url_expired: true`, prompting the user to regenerate the report.

### Data Security

- All endpoints require JWT authentication — only authenticated users can request or check PDFs
- The shop ID is derived from the authenticated user's context, not from a query parameter — users cannot access other shops' data
- Client ID filters are validated against the shop's client records before use
- Rate limiting prevents abuse of the generation endpoints

---

## Relationship with Other Modules

### Booking Module (`booking_v2`)

- `BookingV2` — booking records for the bookings, pendings, payments, and security sections
- `BookedProductVariant` — product names for booking line items
- `PaymentDetailsV2` — individual payment receipts for the payments section
- `RefundDetails` — refund records for calculating cancelled booking penalties
- `SecurityDepositAdjustment` — refund/deduction records for the security section
- `SecurityDepositPayment` — payment method information for security deposits
- `Client` — customer names and phone numbers

### Sales Module (`sales`)

- `Sale` — sale records for the sales and pendings sections
- `SaleItem` — product names and variants for sale line items
- `SalesPaymentDetails` — individual payment receipts for the payments section

### Expenses Module (`expenses_v2`)

- `Expenses` — general shop expenses (rent, utilities, supplies, etc.)
- `ProductVariantExpense` — product-related expenses (damage repairs, maintenance, etc.)

### Tailor Module (`tailored`)

- `TailorOrder` — tailor orders for the sales, pendings, payments, and security sections
- `TailorOrderItem` — product names for tailor order line items
- `CustomerPayment` — customer payments on tailor orders

### Shop Module (`shop`)

- `Shop` — shop name, address, phone, email, and logo for the PDF header section

### S3 Service (`services`)

- `S3Service.upload_pdf()` — uploads the generated PDF bytes and returns a presigned download URL

---

## Summary

The **PDF Service Module** provides a complete, scalable pipeline for generating downloadable financial reports.

Key capabilities include:

- **Asynchronous PDF generation** — all PDFs are generated in Celery background workers with configurable time limits (5 minutes hard limit), keeping the API responsive
- **Two PDF formats** — ledger book (professionally styled with ReportLab, shop branding, color-coded tables, and dynamic summaries) and transaction report (simple line-by-line format)
- **Seven transaction sources** — aggregates data from bookings, sales, expenses, payments (booking + sale + tailor + penalty + security deduction), pendings (booking + sale + tailor), and security deposits (booking + tailor)
- **Dynamic table layouts** — table columns, summary calculations, and expense breakdowns automatically adapt based on which transaction types are included
- **Request deduplication** — identical requests within 15 minutes are automatically reused, preventing wasted server resources
- **Task tracking with PDFTask model** — full lifecycle tracking from pending through processing to completed/failed/expired, with performance metadata (generation time, file size, record count)
- **S3 storage with 24-hour download URLs** — generated PDFs are uploaded to S3 with presigned URLs that include user-friendly download filenames
- **Rate limiting** — 20 PDF generations per hour per user, 60 status checks per minute per user
- **Secure image handling** — shop logos are downloaded with SSRF prevention (domain whitelist), size limits (5 MB), timeout protection (4 seconds), and automatic retry
- **Professional styling** — purple-themed color scheme, shop logo and contact details, page numbers on every page, alternating row colors, and dynamic period labels
- **Optimized database queries** — uses `select_related`, `Subquery`, `StringAgg`, and batch lookups throughout to prevent N+1 query problems when fetching data from multiple models
