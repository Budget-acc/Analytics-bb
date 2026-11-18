# Sales Module

---

## Overview

The **Sales Module** manages all direct product sales made by a shop. Unlike the **Bookings module**, where products are rented for a period and returned, the Sales module handles **permanent product transactions** — items are sold to customers and do not come back.

The module connects directly with:

- **Services module** — Product variants and stock information come from the Services module. Each sale item references a `ProductVariant`.
- **Booking/Client module** — Client records are shared with the Booking system. The same `Client` model from `booking_v2` is used for sales, so a customer who both rents and buys products has a single record.
- **Tailored module** — In v4, the ledger view combines completed tailor orders (where `customer_order_type='sale'`) alongside regular sales into a single day-grouped listing.
- **WhatsApp Integration** — Sale invoices can be generated as PDFs and sent to customers via WhatsApp using the `SalesPDFService` and a Celery background task.
- **Subscriptions** — WhatsApp invoice sending and API access are gated behind subscription checks and role-based permissions.

The module handles:

- Creating and managing sale transactions
- Tracking products sold with per-item pricing
- Processing payments in multiple installments and mixed payment methods
- Optional stock deduction based on shop preferences
- Generating and sending PDF invoices
- Day-grouped ledger views for financial tracking
- Global search across sales, bookings, and tailor orders (v4)

---

## Core Concepts

The sales system is built around three primary models:

```
Sale
   ├── SaleItem (one or more products)
   └── SalesPaymentDetails (one or more payments)
```

Each sale contains:

- General sale information (client, date, amounts, staff)
- A list of products sold (with quantity, price, subtotal)
- Payment records tracking how much was paid and through which method

Unlike bookings, sales use **hard delete** — when a sale is removed, the record is permanently deleted from the database. There is no soft-delete mechanism.

---

## Sale Model

### Purpose

The `Sale` model is the central table of the entire sales module. Every product sale transaction is stored here. It holds information about the shop, the customer, the staff who handled the sale, all financial amounts, the sale date, and the shop-specific invoice ID.

### Shop

Every sale belongs to a specific **shop**. The relationship is defined as `ForeignKey` to `shop.Shop` with `on_delete=CASCADE` and `related_name="sales"`. This ensures that all sale data is scoped per shop and if a shop is deleted, all its sales are removed as well.

### Client

The `client` field links the sale to a customer record from the `booking_v2.Client` model. This is an **optional link** (`null=True, blank=True`) — the field uses `on_delete=SET_NULL`, meaning if the client record is deleted, the sale retains all its data and the client field simply becomes null. The sale history is never lost.

In v3, an existing client can be referenced by ID, or a new client can be created inline during sale creation using `_get_or_create_client()`. The method normalizes the phone number (strips `+91`, leading zeros, takes last 10 digits), title-cases the name, and either finds a matching client or creates a new one.

In v4, the `client` field is no longer used for new sales. Instead, `client_phone` and `client_phone_e164` are stored directly on the sale record.

### Client Phone Fields

The system stores **two versions** of the client's phone number:

- `client_phone` — A `CharField(max_length=15)` that stores the **normalized local phone number** (10 digits). In v3, normalization is done manually by stripping `+91` and leading zeros. In v4, the dedicated `validate_and_normalize_phone()` utility is used instead.
- `client_phone_e164` — A `PhoneNumberField(region="IN")` that stores the phone number in **E.164 international format** (e.g., `+919876543210`). This field is indexed (`db_index=True`) for fast lookups. It is validated and formatted using `validate_and_format_phone()` in v4.

This dual-storage approach exists because:

- The local format is what shop owners type and see in the UI
- The E.164 format is required by WhatsApp APIs for sending invoices
- Having both avoids repeated format conversions at query time

Both fields are optional — a sale can exist without any phone number (walk-in customers).

### Address and Description

- `address` — A `TextField` (nullable) storing the customer's delivery or billing address.
- `description` — A `TextField` (nullable) storing any notes or remarks about the sale. This can be updated after creation via the `partial_update` or `update_sale` endpoints.

### Sale Date vs Created Date

The system stores **two different timestamps** that serve distinct purposes:

**`sale_date`** — A `DateField` with `default=timezone.now`. This represents the **actual date when the sale occurred**. A shop may record a sale that physically happened two days ago. This is the date used for:

- Day-grouped ledger views (grouping sales by day)
- Date range filtering in list and ledger APIs
- Financial reports and accounting

The system accepts dates in both `YYYY-MM-DD` and `DD-MM-YYYY` formats and validates that the year falls between 2000 and the current year plus one.

**`created_at`** — A `DateTimeField` with `default=timezone.now`. This represents **when the sale record was created in the system**. This is used for:

- Ordering sales in list views (`order_by("-created_at")`)
- Audit trails and activity logs
- Determining the most recent records

This separation allows shops to accurately record backdated sales while still tracking when data was actually entered into the system.

### Financial Fields

The sale stores three financial values that together represent the complete picture of what a customer owes and what has been paid:

| Field | Type | Description |
|-------|------|-------------|
| `total_amount` | `PositiveIntegerField` | Total cost of all items before any discount. Calculated as the sum of all `SaleItem.subtotal` values. |
| `discount` | `PositiveIntegerField` | Any discount applied to the sale. Must not exceed `total_amount`. |
| `paid_amount` | `PositiveIntegerField` | How much the customer has paid so far. Updated each time a new payment is added. |

The **balance due** is calculated as: `total_amount - discount - paid_amount`. This value is computed dynamically in the API responses — it is not stored as a separate field.

The `_validate_amounts()` helper enforces these rules:

- `total_amount` must be greater than zero
- `paid_amount` cannot be negative
- `discount` cannot be negative
- `discount` cannot exceed `total_amount`
- `paid_amount` cannot exceed `total_amount - discount`

### Decrease Stock

The `decrease_stock` field is a `BooleanField` with `default=False`. It determines whether inventory should be reduced when the sale is created.

```
decrease_stock = True  → Stock will be reduced for each item sold
decrease_stock = False → Stock will remain unchanged
```

This field exists because the application supports multiple workflows, and different shops follow different stock policies:

**Case 1 — Normal sale:** A product is sold today, stock should decrease immediately.

**Case 2 — Recording historical sales:** Old sales are entered retroactively for accounting purposes. The stock was already adjusted at the time of the physical sale, so the system should not reduce it again.

**Case 3 — Shops not using stock tracking:** Some shops want to track revenue and payments through sales, but they do not use the inventory tracking features at all.

When `decrease_stock` is `True`, the system checks available stock before creating or updating sale items. Available stock is calculated by `_calculate_available_stock()`, which takes into account future booking reservations from the `ProductVariantAvailability` table:

```
available_stock = variant.stock - max(future_booked_quantities)
```

This prevents a sale from reducing stock below what is needed for upcoming bookings.

When a sale with `decrease_stock=True` is **deleted**, the system restores stock for every item by adding the quantity back to the product variant's stock field.

### Staff

The `staff` field is a `ForeignKey` to `shop.Staff` with `on_delete=SET_NULL` (`null=True, blank=True`, `related_name="sales"`). It records which staff member handled the sale. This is used for accountability tracking, activity logs, and appears on invoices. If the staff member is later deleted, the sale retains its data.

### Shop Sale ID (Invoice ID)

Each sale is assigned a `shop_sale_id` — a **string formatted as `SL` followed by a six-digit zero-padded number** that is unique per shop:

```
SL000001
SL000002
SL000003
```

This ID is auto-generated during sale creation inside the model's custom `save()` method. The logic works as follows:

1. When a new sale is being saved (no primary key yet), the system acquires a **database-level lock** using `select_for_update()` on the shop record
2. It reads the shop's `last_sale_id` counter
3. If the counter is at zero (first-time setup), the system scans existing sales matching the `SL######` pattern to find the current maximum ID, or falls back to `shop.sale_start_id` if no sales exist yet — this ensures IDs are never reset or duplicated
4. It increments the counter by one, saves it back to the shop, and formats the value as `SL{next_id:06d}`
5. The entire operation runs inside `transaction.atomic()` to prevent two simultaneous sales from receiving the same ID even under high concurrency

The `shop_sale_id` field has a `UniqueConstraint` on `(shop, shop_sale_id)` and a composite database index on `(shop, shop_sale_id)` for fast lookups. An additional index exists on `(shop, created_at)` for efficient date-ordered queries.

This shop-specific ID is printed on invoices, used in WhatsApp messages, and is the primary reference customers use when communicating with the shop.

### Fields Summary

| Field | Type | Description |
|-------|------|-------------|
| `shop` | ForeignKey → Shop | Shop where the sale occurred. CASCADE delete. |
| `client` | ForeignKey → Client | Optional client reference. SET_NULL on delete. |
| `client_phone` | CharField(15) | Normalized local phone number (10 digits). |
| `client_phone_e164` | PhoneNumberField | International E.164 format phone. Indexed. |
| `address` | TextField | Customer address. Optional. |
| `description` | TextField | Sale notes or remarks. Optional. |
| `sale_date` | DateField | Actual date when the sale occurred. |
| `total_amount` | PositiveIntegerField | Total value before discount. |
| `discount` | PositiveIntegerField | Discount applied. |
| `paid_amount` | PositiveIntegerField | Amount paid so far. |
| `decrease_stock` | BooleanField | Whether stock should be reduced. Default False. |
| `created_at` | DateTimeField | When the record was created in the system. |
| `shop_sale_id` | CharField(10) | Shop-specific invoice ID (e.g., SL000001). Indexed. |
| `staff` | ForeignKey → Staff | Staff member who handled the sale. SET_NULL on delete. |

### Constraints and Indexes

- **UniqueConstraint** on `(shop, shop_sale_id)` — ensures no two sales in the same shop share the same invoice ID
- **Index** on `(shop, created_at)` — optimizes date-ordered queries filtered by shop
- **Index** on `(shop, shop_sale_id)` — optimizes invoice ID lookups within a shop
- **db_index** on `client_phone_e164` — optimizes phone-based searches

---

## SaleItem Model

### Purpose

A single sale may contain **multiple products**. For example, one invoice might include:

```
1 × Shirt    → ₹500
2 × Pants    → ₹1200
1 × Jacket   → ₹2000
```

Each product line is stored as a separate `SaleItem` record. The `SaleItem` model links a sale to a specific product variant and records how many units were sold, at what price, and the subtotal.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `sale` | ForeignKey → Sale | Parent sale. CASCADE delete. `related_name="items"`. |
| `product_variant` | ForeignKey → ProductVariant | The specific product variant sold. CASCADE delete. `related_name="sale_items"`. |
| `quantity` | PositiveIntegerField | Number of units sold. Default 1. |
| `price` | PositiveIntegerField | Selling price per unit **at the time of sale**. |
| `subtotal` | PositiveIntegerField | Calculated as `price × quantity`. Default 0. |
| `created_at` | DateTimeField | Auto-set when the item is created (`auto_now_add=True`). |

### Why Price Is Stored on the Sale Item

The price is stored directly on the sale item (not looked up from the product variant) because **product prices change over time**:

```
Product price today:  ₹500
Price next month:     ₹600
```

The old sale record must always show the price that was charged at the time of the transaction. By storing the price on the `SaleItem`, the system preserves the **historical price** and the sale total remains accurate regardless of future price changes.

If no price is provided during sale creation, the system falls back to the product variant's current price (`variant.price`).

---

## SalesPaymentDetails Model

### Purpose

A sale may be paid using multiple payment methods or across multiple installments. Each payment is stored as a separate `SalesPaymentDetails` record:

```
Total Sale: ₹5000

Payment 1 → ₹2000 (Cash)
Payment 2 → ₹2000 (UPI)
Payment 3 → ₹1000 (GPay)
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `sale` | ForeignKey → Sale | Parent sale. CASCADE delete. `related_name="payments"`. |
| `payment_amount` | IntegerField | Amount paid in this installment. |
| `payment_method` | CharField(10) | Payment type. Choices: `gpay`, `cash`, `upi`. |
| `created_at` | DateTimeField | When this payment was recorded. `default=timezone.now`. |

### Supported Payment Methods

| Value | Display Name |
|-------|-------------|
| `gpay` | GPay |
| `cash` | Cash |
| `upi` | UPI |

### How Payments Work

- During sale creation, if `paid_amount > 0`, a single `SalesPaymentDetails` record is created
- Additional payments can be added later via the **add-payment** endpoint
- When adding a payment, the system validates that `payment_amount <= balance_due` (where `balance_due = total_amount - discount - paid_amount`)
- The sale's `paid_amount` field is incremented by the new payment amount
- Each payment records the method used, allowing a sale to be paid partially in cash and partially via UPI

---

## API Versioning

The Sales module provides APIs in two versions:

### v3 — Base Version

- Full CRUD for sales (create, list, retrieve, update, delete)
- Client resolution by ID or inline creation via `_get_or_create_client()`
- Stock validation using `ProductVariantAvailability`
- Payment management (add payments, mixed methods)
- Combined update endpoint (`update-sale`) for modifying variants + basic info in one call
- Invoice generation and WhatsApp sending via `SalesPDFService` and `send_sale_invoice_task`
- Day-grouped ledger view (`SaleDetailAmountAPIView`) showing sales only
- Basic search by `client_name` and `shop_sale_id`

### v4 — Enhanced Version

Everything in v3, plus:

- **Phone validation** — `_validate_client_phone()` uses the shared `validate_and_normalize_phone()` utility instead of manual normalization
- **E.164 phone support** — `client_phone_e164` is validated via `validate_and_format_phone()` and stored on the sale
- **Enhanced search with ranking** — List endpoint uses a `search_rank` annotation with `Case/When` to rank results by relevance (exact match → starts with → product name match → contains). Searches across `client_phone`, `client__client_phone_1`, `shop_sale_id`, and product names via `Exists` subquery.
- **Activity logging** — `create_activity_log()` is called on `partial_update`, `destroy`, and `update_sale` operations to track who changed what
- **WhatsApp subscription validation** — Before queuing a WhatsApp invoice task, the system validates the shop's subscription status, checks `WHATSAPP_MESSAGE` feature access, and applies rate limiting (max 10 queued tasks per shop per minute via cache)
- **Combined ledger view** — `SaleDetailAmountAPIView` combines both `Sale` records and completed `TailorOrder` records (where `customer_order_type='sale'`) into a single day-grouped listing
- **Global search** — `GlobalSearchViewSet` searches across bookings, sales, and tailor orders with relevance ranking
- **Attribute values** — Retrieve endpoint includes product and variant `attribute_values` for detailed attribute display

### URL Configuration

**v3 URLs:**

| Pattern | View | Description |
|---------|------|-------------|
| `sales/` | `SaleViewSet` | CRUD operations (router-registered) |
| `wallet/sales/` | `SaleDetailAmountAPIView` | Day-grouped ledger view |

**v4 URLs:**

| Pattern | View | Description |
|---------|------|-------------|
| `sales/` | `SaleViewSet` | CRUD operations (router-registered) |
| `wallet/sales/` | `SaleDetailAmountAPIView` | Day-grouped ledger view (with tailor orders) |
| `global-search/` | `GlobalSearchViewSet` | Cross-module search |

---

## Authentication and Permissions

All sale endpoints use:

- `CutoffJWTAuthentication` — JWT-based authentication
- `IsAuthenticated` — User must be logged in
- `SubscriptionActivePermission` — Shop must have an active subscription
- `FeatureRolePermissionFactory("SALES", allowed_roles=["OWNER", "MANAGER", "STAFF"])` — Only users with the OWNER, MANAGER, or STAFF role can access sales features

The `initial()` method on the ViewSet extracts the active shop ID from the request via `get_active_shop_id(request)`. If the shop is deactivated, a `PermissionDenied` error is raised.

---

## SaleViewSet — CRUD APIs

### Helper Methods

Before explaining the CRUD operations, here are the internal helper methods used across multiple endpoints:

**`_get_or_create_client(shop_id, client_data)`** (v3 only)

- Normalizes the phone number: strips `+91`, strips leading zeros, takes last 10 digits, converts to integer
- Title-cases the client name
- Searches for an existing client matching `(shop_id, client_name, client_phone_1)`
- If found, returns the existing client; otherwise creates a new one
- Returns `(client, created_flag)` tuple

**`_validate_client_phone(phone)`** (v4 only)

- Uses the shared `validate_and_normalize_phone()` utility
- Returns `(normalized_phone, error_message)` tuple
- Phone is optional for sales — returns `(None, None)` if no phone is provided

**`_validate_amounts(total_before_discount, paid_amount, discount_amount)`**

- Validates that `total_before_discount > 0`
- Validates that `paid_amount >= 0` and `discount_amount >= 0`
- Validates that `discount_amount <= total_before_discount`
- Validates that `paid_amount <= total_before_discount - discount_amount`
- Returns `(is_valid, error_message)` tuple

**`_calculate_available_stock(variant)`**

- Queries `ProductVariantAvailability` for all future dates where this variant has bookings
- Finds the maximum `booked_quantity` across all future dates
- Returns `variant.stock - max_booked` — this is the stock available for sale without affecting upcoming bookings

**`_verify_variant_belongs_to_shop(variant_id, shop_id)`**

- Checks that the product variant exists, belongs to the specified shop (via `product__shop_service__shop_id`), and is active
- Returns the variant object if valid, `None` otherwise

**`_validate_sale_date(sale_date)`**

- Accepts dates in `YYYY-MM-DD` or `DD-MM-YYYY` format
- Also handles `datetime` objects and `date` objects
- Validates that the year is between 2000 and `current_year + 1`
- Defaults to today's date if no sale_date is provided
- Returns `(date_obj, error_message)` tuple

---

### Create Sale

**Method:** `create(request)` — `POST /sales/`
**Decorator:** `@transaction.atomic`

This is the most complex operation in the module. The entire process runs inside a database transaction — if any step fails, all changes are rolled back.

**Step-by-step logic:**

1. **Handle client information**
   - **v3:** If `client_id` is provided, look up the existing client. Otherwise, create or find a client using `_get_or_create_client()` with the provided `client_name` and `client_phone_1`.
   - **v4:** Validate and normalize `client_phone` using `_validate_client_phone()`. Validate and format `client_phone_e164` using `validate_and_format_phone()`. No client record is created — phone numbers are stored directly on the sale.

2. **Validate staff** — If `staff_id` is provided, verify the staff member exists and belongs to the shop.

3. **Validate ALL product variants** — For each variant in the `variants` array:
   - Verify the variant exists, belongs to the shop, and is active
   - Validate that quantity is greater than zero
   - If `decrease_stock` is `True`, check that the requested quantity does not exceed available stock (accounting for future booking reservations)
   - Validate that the price is greater than zero (falls back to `variant.price` if not provided)
   - Calculate `subtotal = price × quantity` and accumulate `total_before_discount`

4. **Validate financial amounts** — Run `_validate_amounts()` with `total_before_discount`, `paid_amount`, and `discount`.

5. **Validate sale date** — Run `_validate_sale_date()` on the provided date.

6. **Create the Sale record** — The `Sale.objects.create()` call triggers the custom `save()` method, which atomically generates the `shop_sale_id`.

7. **Create SaleItems and reduce stock** — For each validated variant:
   - Create a `SaleItem` record with the sale, variant, quantity, price, and subtotal
   - If `decrease_stock` is `True`, subtract the quantity from the variant's `stock` field and save

8. **Create payment record** — If `paid_amount > 0`, create a `SalesPaymentDetails` record with the payment amount and method (defaults to `"cash"`).

9. **Optional: Send WhatsApp invoice**
   - **v3:** If `send_invoice` is truthy, queues `send_sale_invoice_task.delay(sale.id)` immediately.
   - **v4:** Before queuing the task, performs full validation:
     - Checks that the shop has an active subscription
     - Checks that the user has `WHATSAPP_MESSAGE` feature access via `has_feature_access()`
     - Applies rate limiting using Django's cache: a counter `wa_task_queue_sale_{shop_id}` is checked and incremented with a 60-second timeout window. If more than 10 tasks are already queued for this shop in the last minute, the invoice is not sent.
     - Only if all checks pass is the Celery task queued

10. **Return response** — Returns `sale_id`, `total_amount`, `discount`, `final_amount`, `paid_amount`, and `balance_due`.

---

### List Sales

**Method:** `list(request)` — `GET /sales/`

Returns a paginated list of sales for the current shop, ordered by most recent first.

**v3 search:**

- Filters by `client__client_name__icontains` or `shop_sale_id__icontains`

**v4 search with ranking:**

The v4 list endpoint uses a sophisticated relevance-based search system. When a `search` query parameter is provided:

1. An `Exists` subquery checks if any `SaleItem` for each sale has a product name matching the search query
2. A `search_rank` annotation is added using `Case/When`:
   - Rank 0 (exact match): `client_phone`, `client__client_phone_1`, or `shop_sale_id` exactly matches the query
   - Rank 1 (starts with): Any of these fields starts with the query
   - Rank 2 (product match): A sale item's product name contains the query
   - Rank 3 (contains): Default fallback for partial matches
3. Results are filtered across all searchable fields and ordered by `search_rank` ascending, then `created_at` descending

**Date range filtering** — Both versions support `from` and `to` query parameters to filter by `sale_date`. Dates are parsed in both `YYYY-MM-DD` and `DD-MM-YYYY` formats.

**Pagination** — Uses `StandardPageNumberPagination`.

**Response fields:**

| Field | Description |
|-------|-------------|
| `id` | Sale ID |
| `client_name` (v3) / `client_phone` (v4) | Client identifier |
| `client_phone_e164` (v4 only) | E.164 formatted phone |
| `total_amount` | Total before discount |
| `paid_amount` | Amount paid |
| `discount` | Discount applied |
| `sale_date` | Date of sale |
| `created_at` | Record creation time |
| `products` | Comma-separated product names |
| `staff_name`, `staff_color` (v4) | Staff info |
| `shop_sale_id` (v4) | Invoice ID |

---

### Retrieve Sale

**Method:** `retrieve(request, pk)` — `GET /sales/{id}/`

Returns the complete details of a single sale.

The query uses `select_related` for staff and client, and `prefetch_related` for:

- Sale items with their product variants, products, shop services, general services, and main categories
- Payment records
- (v4) Product and variant `attribute_values` with their related attributes

**Response includes:**

- All sale fields including `balance_due` (calculated as `total_amount - discount - paid_amount`)
- `items` array — Each item contains:
  - `product_name`, `variant` (attribute), `quantity`, `price`, `subtotal`
  - `thumbnail`, `image` (v4), `color`, `category`, `model`, `main_category_name`
  - `attributes` dict (v4) — Product and variant attribute key-value pairs, where variant attributes override product attributes
- `payments` array — Each payment contains `amount`, `method`, and `date`

---

### Add Payment

**Method:** `add_payment(request, pk)` — `PATCH /sales/add-payment/{id}/`
**Decorator:** `@transaction.atomic`

Adds an additional payment to an existing sale. This supports installment payment workflows.

**Logic:**

1. Fetch the sale and validate it exists for the current shop
2. Validate that `payment_amount > 0`
3. Calculate `balance_due = total_amount - discount - paid_amount`
4. Validate that `payment_amount <= balance_due` — cannot overpay
5. Increment `sale.paid_amount` by the payment amount and save
6. Create a new `SalesPaymentDetails` record
7. Return the updated totals and new balance

---

### Update Sale (Combined)

**Method:** `update_sale(request, pk)` — `PATCH /sales/update-sale/{id}/`
**Decorator:** `@transaction.atomic`

This is a **combined update endpoint** that can modify staff, client, variants, and basic info all in a single API call. It uses `select_for_update()` on both the sale and its items to prevent concurrent modifications.

**Logic:**

1. **Lock the sale and items** — Fetch the sale with `select_for_update()` and all its `SaleItem` records also locked with `select_for_update()`
2. **Handle staff update** — If `staff_id` is in the request, validate and assign the new staff member
3. **Handle client update** (v3) — If `client_id` or `client_name` is provided, resolve the client by ID or create a new one
4. **Handle client phone update** (v4) — If `client_phone` or `client_phone_e164` is provided, validate and update the phone fields
5. **Handle variants and discount update** — If `variants` or `discount` is in the request, delegate to `_update_variants_logic()`:

   The `_update_variants_logic()` method performs a **three-step stock-safe variant update**:

   **Step 1 — Validate stock and lock variants:**
   - For each new variant, acquire a row-level lock using `select_for_update()`
   - Calculate the quantity difference between the new and old quantity
   - If `decrease_stock` is `True` and the difference is positive (more items being sold), verify available stock is sufficient

   **Step 2 — Apply changes:**
   - For variants that existed before but are not in the new list: restore stock (if `decrease_stock`), then delete the `SaleItem`
   - For variants that exist in both old and new: adjust stock by the difference, update quantity/price/subtotal
   - For new variants not previously in the sale: create new `SaleItem`, reduce stock if applicable

   **Step 3 — Recalculate totals:**
   - Sum all item subtotals to get the new `total_amount`
   - Apply the new discount (or existing discount if not changed)
   - Validate that discount does not exceed total and final amount is not negative
   - Update `total_amount`, `discount`, and `paid_amount` on the sale
   - Update or create the payment record, or delete all payments if paid amount becomes zero

6. **Handle basic info update** — Delegate to `_update_basic_info_logic()` for description, address, sale_date, and payment_method updates
7. **(v4) Create activity log** — Log all changes made in this operation

---

### Partial Update

**Method:** `partial_update(request, pk)` — `PATCH /sales/{id}/`
**Decorator:** `@transaction.atomic`

Updates basic sale fields without modifying variants.

**Supported fields:**

- `description` — Free text update
- `client_phone` (v4) — Validated via `_validate_client_phone()`
- `sale_date` — Validated via `_validate_sale_date()`
- `discount` — Validated against current `total_amount` and `paid_amount`

In v4, the method tracks which fields changed and creates an activity log entry.

---

### Delete Sale

**Method:** `destroy(request, pk)` — `DELETE /sales/{id}/`
**Decorator:** `@transaction.atomic`

Permanently removes a sale record.

**Logic:**

1. Fetch the sale for the current shop
2. **Restore stock** — If `decrease_stock` was `True`, iterate through all sale items and add back each item's quantity to the product variant's stock
3. (v4) Save the shop and sale ID references before deletion for the activity log
4. Delete the sale (this cascades to delete all `SaleItem` and `SalesPaymentDetails` records)
5. (v4) Create an activity log entry recording the deletion

**v3:** The stock restoration is always performed regardless of the `decrease_stock` flag (a known quirk in v3). **v4:** Stock restoration correctly only happens when `decrease_stock` is `True`.

---

### Update Client (v3 only)

**Method:** `update_client(request, pk)` — `PATCH /sales/update-client/{id}/`

Allows updating the client associated with a sale. Can either assign an existing client by ID or create a new one inline. Also supports updating the delivery address.

---

### Update Variants (v3 only)

**Method:** `update_variants(request, pk)` — `PATCH /sales/update-variants/{id}/`

A standalone endpoint for updating sale items with the same three-step stock-safe logic as `_update_variants_logic()`, plus a final recalculation step that ensures the new total is not less than the already-paid amount.

---

### Send Invoice

**Method:** `send_invoice(request, pk)` — `GET /sales/send-invoice/{id}/`

Generates a PDF invoice and either returns it as a file download or sends it via WhatsApp.

**Logic:**

1. **Fetch sale** — Load the sale with all related objects (client, staff, shop, items with products and categories, payments)

2. **Generate PDF** — Call `SalesPDFService.generate_sale_pdf()` with the shop, client, sale, items, and invoice prefix `"SAL"`. The service returns a base64-encoded PDF string. The system validates the base64 output before proceeding.

3. **Return PDF or send via WhatsApp:**
   - If `send_whatsapp` query parameter is `false` (default): Decode the base64 PDF and return it as an `HttpResponse` with `Content-Type: application/pdf` and a `Content-Disposition` header for file download
   - If `send_whatsapp` is `true`: Initialize `WhatsAppService()` and call `send_sale_invoice_template()` with the client's phone number, name, sale ID, total amount (after discount), the PDF base64, and filename

---

## SaleDetailAmountAPIView — Day-Grouped Ledger View

**Endpoint:** `GET /wallet/sales/`

This API provides a **day-grouped sales listing** designed for the wallet/ledger section of the application. It groups all sales (and in v4, tailor orders) by date, calculates daily totals, and paginates by days rather than individual records.

### DayGroupedPagination

A custom pagination class extending `PageNumberPagination` with `page_size=6`. Instead of paginating individual records, it paginates **day groups** — each page shows 6 days worth of data, regardless of how many sales occurred on each day.

The `paginate_day_grouped()` method accepts an `OrderedDict` keyed by `"Month Year|DD-MM-YYYY"` strings and returns a subset of those keys based on the current page.

### v3 Logic (Sales Only)

1. Query all sales for the shop, optionally filtered by `client_id`
2. For each sale, collect:
   - Product names from sale items
   - Payment methods (converted to display names using `PAYMENT_METHOD_CHOICES`)
   - Sale date and created_at (converted to IST)
3. Group sales by `"Month Year|DD-MM-YYYY"` key (e.g., `"January 2025|15-01-2025"`)
4. Calculate `total_expense` for each day (sum of `total_amount - discount` for all sales that day)
5. Structure as nested `OrderedDict`: Month → Day → `{total_expense, items}`
6. Flatten to day-level keys for pagination
7. After pagination, rebuild the nested month → day structure

### v4 Logic (Sales + Tailor Orders)

In v4, the ledger view combines **two types of records**:

**Sales** — Same as v3, but uses optimized annotation-based queries with `F()` expressions:
```
total_amount_field = F('total_amount') - F('discount')
```

**Tailor Orders** — Queries `TailorOrder` records where:
- `customer_order_type='sale'` (the tailor order represents a sale, not a rental)
- `status='completed'` (only completed orders)
- `deleted__isnull=True` (not soft-deleted)

Uses `completed_at` as the grouping date instead of `sale_date`.

Both record types are combined into a single `combined_data` list. Each item includes a `type` field (`"sale"` or `"tailor_order"`) to distinguish them in the frontend.

Within each day group, items are **sorted by `total_amount` in descending order** (highest amount first) for better UX.

The final response structure:

```json
{
  "January 2025": {
    "15-01-2025": {
      "total_expense": 15000,
      "items": [
        {"id": 5, "invoice_id": "SL000005", "total_amount": 8000, "type": "sale", ...},
        {"id": 3, "invoice_id": "TO000003", "total_amount": 7000, "type": "tailor_order", ...}
      ]
    }
  }
}
```

---

## GlobalSearchViewSet (v4 Only)

**Endpoint:** `GET /global-search/search/`

A cross-module search endpoint that searches across **bookings**, **sales**, and **tailor orders** simultaneously.

### Search Logic

For each of the three record types, the system:

1. Creates an `Exists` subquery to check if any line item's product name matches the search query
2. Annotates a `search_rank` using `Case/When` with four levels:
   - **Rank 0 (exact match):** Invoice ID or client name exactly matches the query
   - **Rank 1 (starts with):** Invoice ID or client name starts with the query
   - **Rank 2 (product match):** A line item's product name contains the query
   - **Rank 3 (contains):** Any searchable field contains the query
3. Filters across client names, staff names, invoice IDs, and product names
4. Annotates `record_type` (`"booking"`, `"sale"`, or `"tailor_order"`) and `sort_date`

### Combining and Sorting

All three querysets are combined using `itertools.chain` and sorted by:
1. `search_rank` ascending (most relevant first)
2. `created_at` descending (most recent first within the same rank)

### Date Filtering

Optional `start_date` and `end_date` query parameters filter results by `created_at__date`. Dates are parsed using `parse_custom_date()` which supports both `DD-MM-YYYY` and `YYYY-MM-DD` formats.

### Response Format

Each result includes a `type` field indicating the record type, plus type-specific fields:

- **Booking:** `id`, `client`, `pickup_date`, `return_date`, `cooling_period_end`, `booking_status`, `delivery_status`, `products`, `staff_color`, `type: "booking"`
- **Sale:** `id`, `client_phone`, `total_amount`, `paid_amount`, `discount`, `sale_date`, `products`, `staff_color`, `type: "sale"`
- **Tailor Order:** `id`, `status`, `customer_order_type`, `pickup_date`, `products`, `staff_color`, `type: "tailor_order"`

Results are paginated using `StandardPageNumberPagination`.

---

## Admin Configuration

The Sales module registers all three models in the Django admin with the following configuration:

### SaleAdmin

- **List display:** id, shop_sale_id, shop, client_phone, client_phone_e164, sale_date, total_amount, discount, paid_amount, decrease_stock, staff, created_at
- **List filters:** shop, sale_date
- **Search fields:** client__client_name, shop__name, client_phone, client_phone_e164
- **Inlines:** SaleItemInline (TabularInline), PaymentDetailsInline (TabularInline)

### SaleItemAdmin

- **List display:** id, sale, product_variant, quantity, price, subtotal, created_at
- **List filters:** sale__shop, product_variant
- **Search fields:** product_variant__name, sale__client__client_name

### PaymentDetailsAdmin

- **List display:** id, sale, payment_amount, payment_method, created_at
- **List filters:** payment_method, sale__shop
- **Search fields:** sale__client__client_name

---

## Relationship with Other Modules

### Services Module (ProductVariant)

Every `SaleItem` references a `ProductVariant` from the Services module. The variant provides the product name, thumbnail, color, category, model, current price, and stock level. When `decrease_stock` is `True`, the sale directly modifies the variant's `stock` field.

### Booking Module (Client, ProductVariantAvailability)

The `Client` model is shared between bookings and sales. The `ProductVariantAvailability` table is consulted during stock calculations — when determining available stock for a sale, the system accounts for the maximum future booked quantity to ensure sales do not consume stock reserved for upcoming bookings.

### Tailored Module (TailorOrder)

In v4, the `SaleDetailAmountAPIView` combines regular sales with completed tailor orders that have `customer_order_type='sale'`. The `GlobalSearchViewSet` also searches across tailor orders alongside bookings and sales.

### WhatsApp Integration

Sale invoices are generated using `SalesPDFService.generate_sale_pdf()` and sent via WhatsApp using `send_sale_invoice_task`, a Celery background task. In v4, sending is gated behind subscription validation and rate limiting.

### Subscriptions

API access requires an active subscription (`SubscriptionActivePermission`). WhatsApp invoice sending additionally requires `WHATSAPP_MESSAGE` feature access, checked via `has_feature_access()`.
