# Expense Module

---

## Overview

The **Expense Module** is responsible for tracking all financial expenses incurred by a shop. It manages two distinct categories of expenses: **product-related expenses** tied to specific inventory items, and **general shop expenses** that cover operational costs not linked to any product. This module is a critical part of the shop's financial ecosystem and connects directly with:

- **Services Module** — Product variant expenses reference `ProductVariant` from the services module
- **Booking Module** — The daywise summary and transaction history APIs aggregate booking payments, pending amounts, and security deposits
- **Sales Module** — Sales transactions and payments are included in the global ledger and export APIs
- **Tailored Module** — Tailor order payments, security deposits, and pending amounts are integrated into the summary and transaction APIs
- **PDF Service Module** — The Excel export view in v3 inherits from `ShopLedgerTransactionExportMixin` provided by the pdf_service module

The module supports:

- Recording and categorizing both product-level and shop-level expenses
- Tracking payment methods (UPI and Cash)
- Generating daily financial summaries (daywise ledger)
- Listing transaction history across all financial sections
- Exporting transaction data as PDF (ReportLab) and Excel (xlsxwriter)
- Date-grouped expense listing with custom pagination (v4)

---

## Soft Delete Policy

Both expense models inherit from `SafeDeleteModel` with the `SOFT_DELETE` policy from the `django-safedelete` library. When a record is deleted, it is not removed from the database. Instead, a `deleted` timestamp is set on the record. All default querysets automatically exclude soft-deleted records, so they become invisible to normal queries without being physically destroyed. This protects against accidental data loss and maintains referential integrity with financial reports that may reference historical expense records.

---

# 1. Expenses Model — General Shop Expenses

The `Expenses` model stores **shop-level operational costs** that are not tied to any specific product. Examples include rent, electricity bills, staff payments, office supplies, and miscellaneous operating expenses.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `shop` | ForeignKey → `shop.Shop` | The shop where this expense occurred. Uses `CASCADE` deletion — if the shop is deleted, all its expenses are removed. The `related_name='expenses'` allows reverse lookups from the Shop model. |
| `user` | ForeignKey → `authentication.User` | The user (staff member or shop owner) who recorded this expense. Set to `SET_NULL` on deletion with `null=True, blank=True`, so the expense record survives even if the user account is removed. |
| `date` | DateField | The actual date the expense occurred. This field is **required** (`null=False, blank=False`). It is used as the primary date for sorting, grouping, and filtering expenses in all APIs. |
| `expense` | IntegerField | The expense amount in the smallest currency unit (rupees). This field is **required** (`null=False, blank=False`). Stored as an integer — decimal amounts are not supported. |
| `type` | CharField (max_length=50) | A free-text category for the expense, such as "Rent", "Electricity", "Salary", "Transport", etc. This field is optional (`null=True, blank=True`). It is used in expense breakdown summaries in Excel exports and for display in the frontend. |
| `description` | TextField | Additional notes about the expense. Optional field. Used for display and in export reports. |
| `payment_method` | CharField (max_length=10) | How the expense was paid. Choices are `"upi"` (displayed as "UPI") and `"cash"` (displayed as "Cash"). Defaults to `"cash"`. |
| `created_at` | DateTimeField (auto_now_add) | Timestamp of when the record was created. Used as a secondary sort key when multiple expenses share the same date. |

### Database Indexes

The model defines two indexes for query performance:

- **`shop` index** — Speeds up the most common query pattern: filtering all expenses for a specific shop
- **`date` index** — Speeds up date range queries used by the daywise summary, transaction history, and export APIs

### String Representation

```
__str__: "{shop.name} - ₹{expense} on {date}"
Example: "Fashion Hub - ₹5000 on 2024-05-15"
```

---

# 2. ProductVariantExpense Model — Product-Related Expenses

The `ProductVariantExpense` model tracks expenses tied to **specific product variants** in the shop's inventory. These expenses represent costs incurred for maintaining, repairing, washing, or servicing individual products.

Example scenarios:

- Repairing a damaged lehenga after a rental
- Dry cleaning a sherwani before re-renting
- Fixing a broken accessory
- Maintenance of electronic equipment

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `product_variant` | ForeignKey → `services.ProductVariant` | The specific product variant this expense relates to. Uses `CASCADE` deletion and `related_name='expenses'`. Through the variant, the system can traverse to the product and shop service to verify ownership. |
| `user` | ForeignKey → `authentication.User` | The user who recorded the expense. Uses `SET_NULL` with `null=True, blank=True` — the expense persists even if the user is deleted. |
| `shop` | ForeignKey → `shop.Shop` | The shop where the expense occurred. Uses `CASCADE` deletion. This field provides a direct shop reference for efficient filtering, rather than requiring a join through `product_variant → product → shop_service → shop`. |
| `date` | DateField | The actual date the expense occurred. Used as the primary date for sorting and grouping. |
| `expense` | IntegerField | The expense amount in rupees. |
| `type` | CharField (max_length=50) | Category of the expense, such as "Repair", "Washing", "Maintenance", "Damage Repair". Optional field. |
| `description` | TextField | Additional notes describing the expense. Optional field. |
| `linked_expense` | ForeignKey → `Expenses` | An optional reference to a general `Expenses` record. Uses `CASCADE` deletion with `null=True, blank=True`. This creates a parent-child relationship where a product variant expense can be associated with a general expense entry. When the linked general expense is deleted, this variant expense is also removed. The linked expense's user is used as a fallback for the user name in API responses. |
| `payment_method` | CharField (max_length=10) | Payment method used — either `"upi"` or `"cash"`. Defaults to `"cash"`. |
| `created_at` | DateTimeField (auto_now_add) | Record creation timestamp. |

### Database Indexes

Same as the `Expenses` model:

- **`shop` index** — Fast shop-scoped filtering
- **`date` index** — Fast date range queries

### String Representation

```
__str__: "{product_variant} - {expense}"  
Example: "Red Lehenga (Size M) - 500"
```

### Key Design Decision: Direct Shop Reference

The `shop` field on `ProductVariantExpense` stores the shop ID directly, even though the shop could be derived by traversing `product_variant → product → shop_service → shop`. This denormalization is intentional — it allows efficient `shop_id` filtering on expense queries without requiring expensive multi-table JOINs. All CRUD operations validate that the product variant actually belongs to the shop by traversing the full relationship chain (`product_variant.product.shop_service.shop_id`).

---

# 3. Serializers

The module uses separate read and write serializers for each model to control which fields are exposed in API responses versus which fields are accepted in request payloads.

### ExpensesReadSerializer

Exposes only the fields needed for display:

- `id`, `date`, `expense`, `payment_method`, `type`, `description`, `created_at`

The `shop` and `user` foreign key IDs are **excluded** from read responses — they are internal fields.

### ExpensesWriteSerializer

Accepts **all model fields** (`fields = '__all__'`). The `date` field accepts two input formats: `YYYY-MM-DD` and `DD-MM-YYYY`, allowing flexibility for both ISO and Indian date formats used by the Flutter frontend.

### ProductVariantExpenseReadSerializer

Exposes: `id`, `product_variant`, `date`, `payment_method`, `expense`, `type`, `description`, `created_at`

### ProductVariantExpensesWriteSerializer

Accepts all model fields. The `date` field also accepts both `YYYY-MM-DD` and `DD-MM-YYYY` formats.

---

# 4. API Versioning

The expense module is served across three API versions:

| Version | URL Prefix | Description |
|---------|-----------|-------------|
| v1 | `/api/v1/expenses/` | Original implementation with basic CRUD and export capabilities |
| v3 | `/api/v3/expenses/` | Enhanced with `StandardResponseMixin` for consistent response formatting, `CutoffJWTAuthentication`, better error handling, and more comprehensive transaction logic including sales, tailor orders, and penalty payments |
| v4 | `/api/v4/expenses/` | Adds day-grouped expense listing with custom pagination, further optimized transaction queries with annotation-based data fetching, and dynamic Excel exports |

Version v2 was skipped — the module jumped from v1 to v3.

---

# 5. Expense CRUD APIs

## 5.1 General Expenses — ExpensesViewSet

**URL Pattern:** Router-registered at `expenses/` (e.g., `/api/v3/expenses/expenses/`)

This ViewSet provides full CRUD operations for general shop expenses. It uses `CutoffJWTAuthentication` and `IsAuthenticated` permissions.

### Shop Resolution

The `initial()` method runs before every action. It calls `get_active_shop_id(request)` to extract the shop ID from the request headers. If the user's access to the shop has been deactivated, a `PermissionDenied` exception is raised immediately, preventing any further processing.

### List (GET)

- Queries all `Expenses` records for the authenticated shop
- Orders by `-created_at` (newest first)
- Uses `StandardPageNumberPagination` for paginated response
- Returns `ExpensesReadSerializer` data

### Retrieve (GET /:pk)

- Fetches a single expense by primary key, scoped to the shop
- Returns 404 with `not_found_response` if the expense does not exist or belongs to a different shop

### Create (POST)

- Copies request data and injects `shop` (from header) and `user` (from authenticated user) automatically
- Validates using `ExpensesWriteSerializer`
- On success, returns the created expense through `ExpensesReadSerializer`
- On validation failure, returns structured validation errors

### Partial Update (PATCH /:pk)

- Fetches the existing expense scoped to the shop
- Applies partial update using `ExpensesWriteSerializer` with `partial=True`
- Returns updated data through `ExpensesReadSerializer`

### Delete (DELETE /:pk)

- Fetches the expense scoped to the shop
- Calls `expense.delete()` — because the model uses `SafeDeleteModel` with `SOFT_DELETE`, this sets the `deleted` timestamp rather than physically removing the record
- Returns a success response with `data=None`

---

## 5.2 Product Variant Expenses — ProductVariantExpensesViewSet

**URL Pattern:** Router-registered at `variant-expenses/` (e.g., `/api/v3/expenses/variant-expenses/`)

This ViewSet manages expenses linked to specific product variants. It performs additional **ownership validation** on every operation to ensure the product variant belongs to the requesting shop.

### Create (POST)

- Injects `shop` and `user` from the request context
- Fetches the `ProductVariant` using `select_related("product__shop_service__shop")` to load the full ownership chain in a single query
- **Validates ownership:** Checks that `variant.product.shop_service.shop_id` matches the authenticated shop's ID. If not, returns a permission denied response
- Saves using `ProductVariantExpensesWriteSerializer` and returns read data

### List (GET)

- Requires a `product_variant` query parameter — if missing, returns a validation error
- Fetches the variant and validates it belongs to the shop via the full ownership chain
- Queries all `ProductVariantExpense` records for that variant and shop
- Orders by `-created_at` and paginates

### Retrieve (GET /:pk)

- Fetches the expense using `select_related` through the full variant → product → shop_service → shop chain
- Validates ownership before returning data

### Partial Update (PATCH /:pk)

- Fetches and validates ownership
- Applies partial update and returns updated data

### Delete (DELETE /:pk)

- Fetches with `select_related` through the full ownership chain
- Validates ownership
- Calls soft delete

---

## 5.3 Frontend Responsibility

The **Flutter frontend application** determines which API to call based on the user's action:

- If the user records an expense against a specific product → call the **ProductVariantExpense API**
- If the user records a general shop expense → call the **General Expense API**

This separation ensures that product-level costs are tracked independently from operational costs.

---

# 6. Expense Listing APIs

## 6.1 ShopExpenseAPIView (v3) — Combined Expense Listing

**Endpoint:** `GET /api/v3/expenses/wallet/expenses/`

This API returns a **combined, paginated list** of both general expenses and product variant expenses for the shop, sorted by date (newest first).

### Logic Step-by-Step

1. **Get shop ID** from the request headers via `get_active_shop_id()`
2. **Fetch owner name** — Queries `ShopUser` for the active owner of the shop. This name is used as a fallback when an expense has no recorded user
3. **Fetch general expenses** — Queries all `Expenses` for the shop with `select_related("user")`. For each expense, builds a dictionary with:
   - `id`, `variant_id` (None), `product_id` (None), `service_id` (None)
   - `date`, `created_at`, `user` (falls back to owner name if user is null)
   - `expense`, `type`, `description`, `is_deleted` (always False for general expenses)
4. **Fetch variant expenses** — Queries all `ProductVariantExpense` for the shop with `select_related` through `linked_expense`, `product_variant`, `product`, and `shop_service`. For each expense:
   - Extracts `variant_id`, `product_id`, `service_id` from the relationship chain
   - User name resolution: first checks `linked_expense.user`, then `exp.user`, then falls back to owner name
   - Sets `is_deleted=True` if the product variant or product no longer exists (soft-deleted products)
5. **Combine and sort** both lists by `(date, created_at)` in descending order
6. **Format dates** — Converts `date` to `DD-MM-YYYY` and `created_at` to `DD-MM-YYYY HH:MM:SS`, then removes the internal sort keys
7. **Paginate** using `StandardPageNumberPagination` and return

---

## 6.2 ShopExpenseGroupedAPIView (v4) — Day-Grouped Expense Listing

**Endpoint:** `GET /api/v4/expenses/wallet/expenses/`

This API returns expenses **grouped by date** with day totals and month headers, using a custom pagination system that ensures no single day is split across pages.

### DayGroupedPagination

A custom paginator that paginates by **day groups** rather than individual items:

- **Page size:** 6 days per page
- The paginator receives an `OrderedDict` of day-grouped data
- It slices by the number of **days** (keys), not individual expense items
- A page always contains complete days — a day's expenses are never split
- Returns `count` (total days), `total_pages`, `current_page`, `next`, `previous` in the response

### Query Optimization

The v4 API uses Django ORM **annotations and `Case`/`When` expressions** to compute fields at the database level instead of in Python:

- **General expenses** are annotated with `variant_id=Value(None)`, `product_id=Value(None)`, `service_id=Value(None)`, `user_name=Coalesce("user__full_name", owner_name)`, `is_deleted=Value(False)`. All output fields have explicit type declarations (`IntegerField`, `CharField`, `BooleanField`)
- **Variant expenses** use `Case(When(...))` to resolve the user name from `linked_expense.user`, then `user`, then owner name fallback. The `is_deleted` flag uses `Case(When(product_variant__isnull=True, ...))` to detect orphaned variants at the database level
- Both querysets use `.values()` to extract only the needed fields, reducing memory overhead

### Grouping and Sorting Logic

1. **Group by date:** Creates a `defaultdict(list)` keyed by a composite key `"Month Year|DD-MM-YYYY"` (e.g., `"May 2024|15-05-2024"`)
2. **Sort days:** Sorts the grouped keys by date in descending order (newest first)
3. **Structure with totals:** For each day, calculates the `total_expense` as the sum of all expense amounts in that day, and sorts individual items within the day by `created_at` (newest first)
4. **Paginate days:** Applies `DayGroupedPagination` to the structured data
5. **Group by month:** The paginated days are then re-grouped by month for the final response

### Response Format

```json
{
  "status": "success",
  "message": "Successfully retrieved",
  "data": {
    "count": 25,
    "total_pages": 5,
    "current_page": 1,
    "next": "http://.../wallet/expenses/?page=2",
    "previous": null,
    "data": {
      "May 2024": {
        "15-05-2024": {
          "total_expense": 1500,
          "items": [
            {
              "id": 42,
              "variant_id": null,
              "product_id": null,
              "service_id": null,
              "expense": 1000,
              "type": "Rent",
              "description": "Monthly rent",
              "user": "Anil Kumar",
              "created_at": "15-05-2024 14:30:00",
              "is_deleted": false,
              "payment_method": "upi"
            },
            {
              "id": 41,
              "variant_id": 15,
              "product_id": 8,
              "service_id": 3,
              "expense": 500,
              "type": "Washing",
              "description": "Dry clean after rental",
              "user": "Anil Kumar",
              "created_at": "15-05-2024 10:15:00",
              "is_deleted": false,
              "payment_method": "cash"
            }
          ]
        }
      }
    }
  }
}
```

---

# 7. Daywise Shop Summary API

**Endpoint:** `GET /api/v3/expenses/wallet/daywise-summary/`

The `DaywiseShopSummaryAPIView` is a comprehensive financial summary API that aggregates data across **multiple modules** to provide a complete daily financial snapshot for a shop. It is one of the most complex views in the expenses module.

### Query Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `date` | No | The date to summarize. Accepts `YYYY-MM-DD` or `DD-MM-YYYY`. Defaults to today (`localdate()`) if not provided. |
| `client_id` | No | Optional client filter. If provided, all aggregations are scoped to transactions involving this specific client. |

### Response Sections

The API returns five aggregated sections:

#### 1. Expenses Summary

Sums the `expense` field from both `Expenses` and `ProductVariantExpense` records for the given date and shop:

- `general_expense`: Sum from `Expenses` table
- `variant_expense`: Sum from `ProductVariantExpense` table
- `total_expense`: Sum of both

#### 2. Payments Summary

Aggregates payments from **four sources**, split by payment method (UPI/GPay vs Cash):

- **Booking payments** — From `PaymentDetailsV2` where the booking is not deleted and not cancelled, filtered by `created_at__date`
- **Penalty payments** — From cancelled bookings. For each cancelled booking, the system calculates: `penalty_amount = advance_amount - total_refund`. The total refund is computed via a subquery on `RefundDetails`. The payment method is taken from the last payment (`sample_method`). Only bookings with `penalty_amount > 0` are included
- **Sales payments** — From `SalesPaymentDetails` filtered by `created_at__date`
- **Tailor order payments** — From `CustomerPayment` for non-deleted tailor orders filtered by `created_at__date`

All four sources are summed together for `gpay_total`, `cash_total`, and `total`.

#### 3. Pendings Summary

Calculates total pending amounts across all unpaid bookings (not just for the given date — this is an **overall pending total** for the shop):

- Formula: `pending = total_amount - advance_amount - discount_amount`
- Only includes bookings where `payment_status=False`, `deleted__isnull=True`, and `delivery_status != 'cancelled'`
- Only includes bookings where the net pending amount is positive

#### 4. Security Amount Summary

This is the most complex section in the summary. It aggregates security deposit data from both bookings and tailor orders:

**Booking security:**
- `booking_security_total` — Sum of `security_amount` from all non-cancelled bookings with security > 0
- `booking_refunded_from_adjustments` — Sum of `SecurityDepositAdjustment` records with `adjustment_type='refund'`
- `booking_deducted` — Sum of `SecurityDepositAdjustment` records with `adjustment_type='deduction'`
- **Completed bookings without entries** — For bookings with `booking_status='completed'` that have **no adjustments and no payments**, the entire security amount is considered automatically refunded. This handles the case where a booking completes cleanly and the security is returned without any explicit adjustment record
- `booking_pending` = `booking_security_total - (booking_refunded + booking_deducted)`, clamped to 0 minimum

**Tailor order security:**
- Includes only tailor orders that have **not been converted to bookings** (`created_booking__isnull=True`)
- Tailor orders have no refund/deduction mechanism — the entire security amount is considered pending

**Combined totals:**
- `total_security_amount` = booking + tailor security
- `total_refunded_amount` = booking refunded only
- `total_deducted_amount` = booking deducted only
- `total_pending_amount` = booking pending + tailor pending

#### 5. Bookings Summary

Sums `total_amount` from all bookings created on the given date.

### Response Format

```json
{
  "status": "success",
  "data": {
    "date": "15-05-2024",
    "payments": { "gpay": 5000, "cash": 3000, "total": 8000 },
    "expenses": { "total": 2500 },
    "pendings": { "total": 15000 },
    "security": {
      "total": 10000,
      "total_refunded": 3000,
      "total_deducted": 1000,
      "total_pending": 6000
    },
    "bookings": { "total": 25000 }
  }
}
```

---

# 8. Transaction History and Export APIs

The module provides a transaction history system that aggregates data from **six financial sections** across the entire shop. This data can be viewed as a paginated list, or exported as PDF or Excel.

## 8.1 ShopTransactionExportMixin

This mixin class is the core of the transaction aggregation logic. Both v3 and v4 versions exist, with v4 being more comprehensive and optimized.

### Date Parsing

The `parse_date()` method accepts date strings in either `YYYY-MM-DD` or `DD-MM-YYYY` format. It tries both formats sequentially and returns `None` if neither works.

### Request Validation (get_transaction_data)

1. Extracts `shop_id`, `from_date`, `to_date`, `type` (comma-separated), and `client_id` from query parameters
2. Validates that both dates are provided and that `from_date <= to_date`
3. If `client_id` is provided, validates the client exists for the shop
4. Delegates to `get_transactions()` for the actual data fetching

### Transaction Fetching (get_transactions)

The method uses a `namedtuple("Txn", ["sort_date", "data"])` to hold each transaction with a sortable date and the response dictionary. Transactions and pending transactions are collected into separate lists.

The `txn_types` parameter controls which sections are included. It can be `"all"` to include everything, or a comma-separated list like `"expense,payments"`.

---

## 8.2 Six Transaction Sections

### Section 1: Expenses

- Fetches both `Expenses` (general) and `ProductVariantExpense` (variant) records filtered by date range
- For general expenses: uses the expense `type` field as the description, falls back to "General expense"
- For variant expenses: uses `linked_expense.user` for the user name, and includes the product name in the description
- v4 additionally appends payment method info to the description (e.g., "Rent (Paid via UPI)")
- Sort date: The expense `date` field converted to a timezone-aware datetime

### Section 2: Payments

Aggregates payments from **four sources** (in v3/v4):

1. **Booking payments** — From `PaymentDetailsV2`, excluding cancelled bookings. Filtered by `created_at__date` range
2. **Penalty payments from cancelled bookings** — Calculates `penalty_amount = advance_amount - total_refunds`. Only included when penalty > 0. Uses the last refund time or booking creation time for date filtering
3. **Sales payments** — From `SalesPaymentDetails`
4. **Tailor order payments** — From `CustomerPayment` for non-deleted orders

Payment methods are normalized: `gpay` and `upi` → `"UPI"`, `cash` → `"Cash"`.

### Section 3: Security

- **Booking security** (v3): All bookings with `security_amount > 0` in the date range. v4 filters further to `booking_status="upcoming"` and excludes cancelled bookings
- **Tailor order security** (v4): Non-converted tailor orders (`created_booking__isnull=True`) with security > 0

### Section 4: Pendings

- **Booking pendings**: Bookings filtered by `return_date` (not creation date) within the date range, where `payment_status=False` and `pending_amount > 0`. Pending formula: `total_amount - discount_amount - advance_amount`
- **Tailor order pendings** (v4): Uses `pickup_date` for sales or `return_date` for rentals, with formula: `customer_total_charge - paid_amount - discount_amount`
- Pending transactions are sorted in **ascending order** (oldest first), while all other transactions are sorted **descending** (newest first). The final list concatenates non-pending (descending) + pending (ascending)

### Section 5: Bookings

- All bookings created in the date range (v4 excludes cancelled bookings)
- Amount field: v3 uses `total_amount`, v4 uses `effective_amount = total_amount - discount_amount`

### Section 6: Sales

- Fetches `Sale` objects with prefetched items and payments
- Each sale includes an `items` list of product names (e.g., `["Red Lehenga", "Gold Necklace"]`)
- Amount: `total_amount - discount`
- **v4 additionally includes completed tailor orders** with `customer_order_type='sale'` as sales transactions

---

## 8.3 ShopTransactionHistoryAPIView

**Endpoint:** `GET /api/v3/expenses/transaction-history/` (also v4)

Returns the aggregated transaction data from `ShopTransactionExportMixin` as a JSON response.

### Query Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `from_date` | Yes | Start date (YYYY-MM-DD or DD-MM-YYYY) |
| `to_date` | Yes | End date (YYYY-MM-DD or DD-MM-YYYY) |
| `type` | No | Comma-separated transaction types: `expense`, `payments`, `security`, `pendings`, `bookings`, `sales`. Defaults to `"all"` |
| `client_id` | No | Filter transactions to a specific client |

---

## 8.4 ExportTransactionsPDFView

**Endpoint:** `GET /api/v3/expenses/export-transactions/pdf/` (also v4)

Generates a simple PDF report using **ReportLab**. Each transaction is rendered as a single line:

```
[DD-MM-YYYY] Type | User/Name | ₹Amount | Description
```

- User names are truncated to 20 characters, descriptions to 50 characters
- v4 additionally includes an "Items" field for sale transactions
- Pages are auto-created when the Y position drops below 50

---

## 8.5 ExportTransactionsExcelView

### v3 — Advanced Excel with Per-Type Formatting

**Endpoint:** `GET /api/v3/expenses/export-transactions/excel/`

The v3 Excel export inherits from `ShopLedgerTransactionExportMixin` (from the pdf_service module) for fetching transaction data, and provides **highly customized Excel formatting** based on the requested transaction types.

The view normalizes input type names (e.g., `"expense"` → `"expenses"`, `"sales"` → `"sales"`) using a `TYPE_MAPPING` dictionary before passing them to the mixin.

#### Single-Type Export Formats

When only one transaction type is requested, the Excel sheet gets **specialized columns and summary sections**:

| Type | Columns | Summary |
|------|---------|---------|
| **Bookings** | Date, Product, Client Name, Paid Amount, Pending Amount, Total Amount | TOTAL row for paid, pending, and total amounts |
| **Payments** | Date & Time, Customer Name, Payment Method, Amount | TOTAL UPI, TOTAL CASH, GRAND TOTAL |
| **Pendings** | Date, Customer Name, Product, Phone, Amount | TOTAL amount |
| **Expenses** | Date, Name, Payment Method, Amount | TOTAL + Expense Breakdown by category |
| **Sales** | Date, Product Name, Payment Method, Amount | TOTAL sales amount |
| **Security** | Date & Time, ID, Name, Amount, Deducted, Refunded | TOTAL AMOUNT, TOTAL DEDUCTED, TOTAL REFUNDED, TOTAL PENDING |

#### Special Case: Expenses + Payments Combined

When both `expenses` and `payments` are requested together, a combined format is used:

- Columns: Date & Time, Type, Customer / Category, Payment Method, Amount
- Summary includes: TOTAL PAYMENTS, TOTAL EXPENSES, BALANCE (payments - expenses)
- An expense breakdown section is appended

#### Multiple / Generic Export

For any other combination or `"all"`, a generic format is used:

- Columns: Date & Time, Type, Customer/Category, Payment Method, Amount
- TOTAL row at the bottom
- Expense breakdown if expenses are present

#### Filename Convention

The exported filename follows the pattern: `{type}_{from_date}_{to_date}.xlsx`

Examples: `all_2024-01-01_2024-01-31.xlsx`, `expenses_2024-05-01_2024-05-31.xlsx`, `expenses_payments_2024-05-01_2024-05-31.xlsx`

### v4 — Simplified Dynamic Excel

**Endpoint:** `GET /api/v4/expenses/export-transactions/excel/`

The v4 version uses its own `ShopTransactionExportMixin` (not the pdf_service one) and produces a simpler, dynamic Excel format:

- **Columns:** Date, Type, Name, Amount, Description
- If any `sale` type transaction exists in the data, an additional **Items** column is dynamically added
- No per-type customization — all transaction types share the same column layout

---

# 9. URL Configuration

### v1 URLs (`/api/v1/expenses/`)

```
router.register('expenses', ExpensesViewSet)
router.register('variant-expenses', ProductVariantExpensesViewSet)

wallet/expenses/              → ShopExpenseAPIView
wallet/daywise-summary/       → DaywiseShopSummaryAPIView
transaction-history/          → ShopTransactionHistoryAPIView
export-transactions/pdf/      → ExportTransactionsPDFView
export-transactions/excel/    → ExportTransactionsExcelView
```

### v3 URLs (`/api/v3/expenses/`)

Same URL patterns as v1, but pointing to v3 view implementations with `StandardResponseMixin`, `CutoffJWTAuthentication`, and enhanced transaction logic.

### v4 URLs (`/api/v4/expenses/`)

```
wallet/expenses/              → ShopExpenseGroupedAPIView (day-grouped)
transaction-history/          → ShopTransactionHistoryAPIView
export-transactions/pdf/      → ExportTransactionsPDFView
export-transactions/excel/    → ExportTransactionsExcelView
```

Note: v4 does **not** include CRUD ViewSets or the daywise summary API — those continue to be served from v3.

---

# 10. Admin Configuration

Both models are registered in the Django admin with a `CommonSafeDeleteAdmin` base class that adds:

- Standard `SafeDeleteAdmin` actions (undelete, etc.)
- A custom **"hard_delete_selected"** action that permanently removes records from the database (with a confirmation dialog)

### ExpensesAdmin

- **Display columns:** id, shop, user, expense, type, date, description, created_at, deleted
- **List filters:** type, date, shop
- **Search fields:** description

### ProductVariantExpenseAdmin

- **Display columns:** id, product_variant, user, expense, date, type, created_at, deleted
- **List filters:** type, date
- **Search fields:** description

---

# 11. Relationship with Other Modules

### Services Module

Product variant expenses reference `ProductVariant` through a ForeignKey. This enables:

- Tracking maintenance cost per individual product item
- Identifying which products are the most expensive to maintain
- The ownership validation chain: `ProductVariant → Product → ShopOwnerService → Shop`

### Booking Module

The daywise summary and transaction history APIs query:

- `BookingV2` — for booking totals, pending amounts
- `PaymentDetailsV2` — for payment data
- `SecurityDepositAdjustment` — for security refunds and deductions
- `SecurityDepositPayment` — for detecting completed bookings without adjustment entries
- `RefundDetails` — for calculating penalty amounts on cancelled bookings
- `Client` — for client name resolution and client-scoped filtering

### Sales Module

- `Sale`, `SaleItem`, `SalesPaymentDetails` — Sales transactions, item names, and payment breakdowns are included in the transaction history and export APIs

### Tailored Module

- `TailorOrder` — Security deposits and pending amounts from tailor orders are included in the daywise summary and transaction APIs
- `CustomerPayment` — Tailor order payments are aggregated in the payment section
- v4 additionally includes completed tailor orders as sales transactions

### PDF Service Module

The v3 `ExportTransactionsExcelView` inherits from `ShopLedgerTransactionExportMixin` (from `pdf_service.v3.views`) for transaction data fetching. It also imports the `safe_decimal` utility function for safe Decimal conversion.

---

# Summary

The **Expense Module** is a comprehensive financial tracking system that goes well beyond simple expense recording. It manages two types of expenses (general shop and product-variant), provides daily financial summaries aggregating data from bookings, payments, sales, tailor orders, and security deposits, and offers transaction history with PDF and Excel export capabilities.

Key capabilities:

- **CRUD operations** for both general and product variant expenses with ownership validation
- **Soft delete** via SafeDeleteModel to preserve financial history
- **Combined expense listing** (v3) and **day-grouped expense listing** (v4) with custom pagination
- **Daywise financial summary** aggregating expenses, payments (4 sources), pendings, security deposits (with refund/deduction tracking), and booking totals
- **Transaction history** across 6 financial sections: expenses, payments, security, pendings, bookings, and sales
- **PDF export** using ReportLab for simple transaction reports
- **Excel export** with per-type column customization, summary sections, and expense breakdown analysis
- **Payment method tracking** (UPI/Cash) across all expense records and export outputs
