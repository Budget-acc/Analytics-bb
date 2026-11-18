# Booking Module – Rental Application

---

## Overview

The **Booking Module** is one of the core components of the rental application. Its primary responsibility is to manage the complete **rental lifecycle of products** — from the moment a customer places a booking, through payment, delivery, return, and final settlement.

A customer can rent one or more products for a specific time period. The system tracks every step of this process and ensures that **inventory availability is always maintained correctly**. The module connects directly with:

- Product inventory
- Payment systems
- Ledger tracking
- Security deposit workflow
- Tailoring / Customization module
- WhatsApp invoice delivery

The module handles:

- Creating and managing bookings
- Managing rented products with per-item return tracking
- Processing payments in multiple installments
- Collecting and settling security deposits
- Tracking physical product delivery and return
- Preventing double bookings through a dedicated availability table
- Managing cooling periods between rentals

---

## Booking Lifecycle

A rental booking moves through a well-defined sequence of stages:

1. Customer selects products
2. System checks product availability for the requested dates
3. Booking is created after all validations pass
4. Customer makes one or more payments
5. Products are delivered to the customer
6. Products are returned (fully or partially)
7. Security deposit is settled through refund or deduction
8. Booking is marked as **Completed**

Each of these stages has corresponding status fields and database records that allow the shop to track exactly where a booking stands at any point in time.

---

## BookingV2 – The Main Booking Record

The `BookingV2` model is the central table of the entire module. Every rental booking is stored here. It holds information about the shop, the customer, the staff who created the booking, all financial amounts, dates and times, the current status of the booking, and references to uploaded documents.

### Shop, Customer, and Staff

Every booking belongs to a specific **shop**. The shop relationship ensures that all data — bookings, products, availability records — is scoped correctly per shop and never mixed across shops.

The **client** field links the booking to a customer record from the `Client` model. This is an optional link — if the client record is deleted, the booking retains its data because the field is set to `SET_NULL`, meaning the booking history is never lost.

The **staff** field is a foreign key to the `Staff` model from the Shop module. It records which staff member created or handled the booking. This is used for accountability tracking and also appears on invoices. In addition, the booking stores a `staff_name` field — a plain `CharField` — which holds the staff member's name at the time the booking was created. This serves as a snapshot so that even if the staff record is later modified or deleted, the booking always knows who originally handled it.

The booking also has two separate client references:

- `client` — A foreign key to `ClientV2`, the legacy client model. This stores the original client link.
- `client_identity` — A foreign key to the `Client` model, which is the currently active client model with E.164 phone number support.

Both are set to `SET_NULL` on deletion, so the booking record is never lost even if the associated client is removed. The `client_address` field is a `TextField` that stores the customer's address directly on the booking, independent of whatever is stored on the client record. This ensures the address at the time of booking is preserved.

### Financial Fields

The booking stores several financial values that together represent the complete picture of what a customer owes and what has been paid:

- `total_amount` — Total cost of the rental
- `advance_amount` — How much the customer has already paid (updated each time a new payment is added)
- `discount_amount` — Any discount applied to the booking
- `security_amount` — Security deposit expected from the customer, tracked and settled separately through the security deposit models

### Completion Tracking

The `completed_at` field is a `DateTimeField` that records the exact timestamp when the booking was marked as completed. This is set only when the booking transitions to the `completed` status. Together with `created_at` (which records when the booking was first created), the system can calculate the full duration of a booking from creation to completion. The `completed_at` value is also used by the document retention system to determine when booking documents can be cleaned up.

### Additional Metadata

The `details` field is a `JSONField` that stores extra booking metadata and notes. This can include any supplementary information about the booking that does not fit into the structured fields — such as extra notes from the staff, special handling instructions, or event-specific details. This field is optional and can be null.

### Pickup and Return Dates

The `pickup_date` and `return_date` fields are `DateTimeField` values that store the full date and time of when the product is picked up and when it must be returned. These are the **primary date fields** used by all system logic — availability checks, cooling period calculations, and booking conflict detection all rely on these values.

In addition, the system stores `original_pickup_date` and `original_return_date`. These fields capture the dates at the time of booking creation and are **never modified afterward**, even if the main pickup and return dates are later updated. This design decision is deliberate:

- It allows the system to always know what the customer originally agreed to
- It preserves an accurate audit trail for reporting, disputes, and invoice generation

### Why Pickup and Return Time Fields Are Stored Separately

Even though date and time information already exists within the `pickup_date` and `return_date` datetime fields, the system additionally stores `pickup_time` and `return_time` as separate `TimeField` values. This is done specifically to support the **Flutter mobile application**:

- When a user manually enters a specific time in the mobile app's time picker, that value is stored in these dedicated fields
- If no manual time is entered, the system falls back to the time embedded within the datetime fields
- This dual-storage approach avoids breaking either the web or mobile experience

### Booking Status

The `booking_status` field tracks the overall lifecycle stage of a booking:

| Status | Meaning |
|--------|---------|
| `upcoming` | Booking is confirmed and the rental period is upcoming or active |
| `completed` | All products returned, all payments cleared, security deposit settled |

A booking can only be marked **Completed** when all three conditions are satisfied:

- Every booked product has been returned
- All outstanding payments have been cleared
- The security deposit has been either refunded or deducted

The system enforces this during the status update API.

### Delivery Status

The `delivery_status` field tracks the **physical movement of products** independently from the booking status:

| Status | Meaning |
|--------|---------|
| `booked` | Booking confirmed, product not yet dispatched |
| `ready to deliver` | Product has been prepared for dispatch |
| `delivered` | Product has been handed to the customer |
| `returned` | Product has been returned by the customer |
| `cancelled` | Booking has been cancelled |

This separation between booking status and delivery status is important. A booking can be `upcoming` while the delivery status is `delivered` — meaning the customer has the product but the rental period has not ended. This granularity helps shop staff track exactly what is happening with each booking at any moment.

### Rental Duration Type

The `rental_duration_type` field determines how the rental charge is calculated:

| Value | Meaning |
|-------|---------|
| `daily` | Charges applied per day (default) |
| `hourly` | Charges applied per hour |

The default is `daily`, which covers most shop use cases. The `hourly` type allows shops that rent cameras, equipment, or event items on short-term hourly rates to use the same system.

### Purchase Mode

| Value | Meaning |
|-------|---------|
| `normal` | Standard rental booking |
| `package` | Package-based rental where multiple items are bundled |

### Shop-Specific Booking ID

Each booking is assigned a `shop_booking_id` — a **six-digit zero-padded number** that is unique per shop. Examples: `000001`, `000123`, `001500`.

This ID is auto-generated during booking creation. The logic works as follows:

- When a new booking is being saved, the system acquires a **database-level lock** (`select_for_update`) on the shop record
- It reads the shop's `last_booking_id` counter, increments it by one, saves it back to the shop, and assigns the formatted value to the booking
- This locking mechanism prevents two simultaneous bookings from receiving the same ID even under high concurrency
- If the shop's counter is at zero (first-time setup), the system first scans existing bookings to find the current maximum ID, so it never resets or duplicates IDs

This shop-specific ID is printed on invoices, used in WhatsApp messages, and is the primary reference customers use when communicating with the shop.

### Documents

The `documents` field stores a **JSON list of S3 URLs** pointing to uploaded documents such as ID proof, rental agreements, or verification documents:

- Each URL is validated before saving using Django's `URLValidator`
- The `documents_details` field stores additional metadata about each document, including upload status tracking (`status`, `total_count`, `uploaded_count`) that is updated as the asynchronous upload tasks complete
- This design allows an unlimited number of documents to be attached to a booking without requiring a separate table

The model's `clean()` method performs strict validation on the `documents` field:

- The field must be a list or tuple
- Each entry must be a non-empty string
- Each string must be a valid URL, verified using Django's `URLValidator`
- Invalid entries raise a `ValidationError` with the specific index that failed

### Soft Delete

The `BookingV2` model uses `SafeDeleteModel` with the `SOFT_DELETE` policy. When a booking is deleted through the API, it is not physically removed from the database — instead, a `deleted` timestamp is set. This preserves the complete booking history, payment records, and audit trail. This is critical for financial reporting and dispute resolution.

### Database Indexes

The `BookingV2` model defines multiple composite and single-field indexes to optimize the most common query patterns:

- `(shop, shop_booking_id)` — For looking up a booking by its shop-specific ID
- `(shop, pickup_date)` — For listing bookings by pickup date within a shop
- `(shop, created_at)` — For listing bookings in creation order within a shop
- `(cooling_period_end)` — For availability checks against cooling period windows
- `(pickup_date)` — For global pickup date queries
- `(return_date)` — For global return date queries
- `(booking_status)` — For filtering by booking status

---

## Cooling Period

### What Is the Cooling Period?

The **Cooling Period** is a time buffer during which a product cannot be booked by any new customer. After a product is returned, shops often need time to:

- Clean the product
- Inspect it for damage
- Carry out any repairs
- Prepare it for the next rental

Similarly, before a product goes out for pickup, some shops want to reserve time to prepare and pack it. The cooling period enforces this buffer automatically within the availability system.

During the cooling period, the `ProductVariantAvailability` records reflect the product as unavailable, and any new booking request that falls within this window will be rejected.

### Two Configurations of Cooling Period

#### Configuration 1 — Cooling Period After Return Date

In this setup, the cooling period begins immediately after the product's return date:

```
Return Date:    March 15
Cooling Period: 2 days

Product blocked:   March 15, March 16
Product available: March 17 onwards
```

This configuration is used by shops that need post-return time for washing, repair, inspection, and preparation.

#### Configuration 2 — Cooling Period Before Pickup Date

In this setup, the cooling period is applied before the next booking's pickup date:

```
Pickup Date:    March 15
Cooling Period: 2 days

System blocks:  March 13, March 14
```

During this blocked time, no other booking can take the product because the shop needs it available to prepare for the upcoming customer. This configuration is used by shops that want to guarantee readiness before the customer arrives.

### How the System Enforces the Cooling Period

The `cooling_period_end` field on the `BookingV2` model stores the exact datetime until which the product is blocked:

- When availability is checked for a new booking, the system compares the requested pickup date against existing bookings' `cooling_period_end` values
- If the requested dates fall within any cooling window, the booking is rejected even if no other active booking occupies those dates
- The availability check is not just "is the product booked on this date?" but rather "is the product booked **or in a cooling period** on this date?"

### Cooling Period Scenarios

**Scenario 1 — Cooling Period After Return:**

```
Booking 1
  Pickup:  March 10
  Return:  March 15
  Cooling: 2 days (after return)

Next booking can start: March 17
```

**Scenario 2 — Cooling Period Before Pickup:**

```
Booking 1
  Pickup:  March 10
  Return:  March 15
  Cooling: 2 days (before pickup)

New booking on March 17 → system blocks March 15 → March 17 for preparation
```

### Key Rule

A product must not be available for booking during any cooling period window, regardless of whether that window falls before a pickup date or after a return date. This rule is enforced at both:

- The **availability query stage** — when the product list is shown to the user
- The **booking creation validation stage** — as a second check before confirming the booking

---

## ProductVariantAvailability – Preventing Double Bookings

### Purpose

The `ProductVariantAvailability` table is the **foundation of the inventory management system**. It stores a record for each product variant for each date it is booked, along with how many units are booked on that date. Without this table, the system would have no efficient way to answer the question "how many units of this product are still available on March 15?"

### How Records Are Created

When a booking is created, the system iterates over every date between `pickup_date` and `return_date` (inclusive) for each booked product variant. For each date, it either creates a new record or increments the `booked_quantity` on an existing one:

```
Customer books 1 Shirt from March 12 to March 15

Records created:
  Shirt | March 12 | booked_quantity: 1
  Shirt | March 13 | booked_quantity: 1
  Shirt | March 14 | booked_quantity: 1
  Shirt | March 15 | booked_quantity: 1

Second customer books 1 more Shirt for March 14 to March 15:
  Shirt | March 14 | booked_quantity: 2
  Shirt | March 15 | booked_quantity: 2
```

### How Double Bookings Are Prevented

Before creating a booking, the system queries the availability table for all dates in the requested range. For each date, it checks:

```
booked_quantity + requested_quantity > total_stock  →  REJECT
```

If this condition is true for even a **single date**, the booking is rejected entirely.

**Example:**

```
Product: Shirt | Total Stock: 2

Existing Booking (Customer A):
  Quantity: 1 | Dates: March 12 to March 15

New Request (Customer B):
  Quantity: 2 | Dates: March 15 to March 17

Check on March 15:
  booked (1) + requested (2) = 3 > stock (2)  REJECTED

Customer B must reduce quantity or choose different dates.
```

### When Availability Records Are Updated

The availability table is updated in all of the following situations:

- **Booking created** — records are inserted or incremented for each date in the booking range
- **Booking updated** — old records are decremented, new records are created for the updated range
- **Booking cancelled** — all related records are decremented so the stock returns to available
- **Booking extended** — new records are added for the extension dates
- **Product returned (partial or full)** — records for dates beyond the actual return date are decremented

### Table Structure

Each `ProductVariantAvailability` record stores:

- `variant` — Foreign key to the `ProductVariant` being tracked (indexed)
- `date` — The specific calendar date
- `booked_quantity` — How many units of this variant are booked on this date
- `created_at` and `updated_at` — Timestamps for record lifecycle

The model enforces a `unique_together` constraint on `(variant, date)`, meaning only one record can exist per variant per date. When a second booking adds quantity for the same variant on the same date, the existing record's `booked_quantity` is incremented rather than creating a duplicate.

---

## Main Booking Flow

### Step 1 — Check Product Availability

Before the customer even sees products, the frontend calls the **Product Availability API**. This API accepts a date range and a shop, and returns all product variants that have sufficient stock for every day in that range. Products that are fully booked, in a cooling period, or have insufficient stock are excluded from the response.

### Step 2 — Show Available Products

The frontend renders only the products that the availability API returned. This prevents users from selecting products that cannot actually be booked, reducing the chance of booking failures.

### Step 3 — Create Booking with Re-Validation

When the customer confirms the booking, the system does **not blindly trust the earlier availability check**. It performs a second, full availability validation at the moment of booking creation. This is necessary because between the time the customer viewed the product list and the time they clicked confirm, another customer may have booked the same product. This double-check prevents race conditions from causing double bookings.

### Step 4 — Booking Overlap and Time Check

The final validation checks for both date-level and time-level conflicts:

- The **date-level check** uses `ProductVariantAvailability`
- The **time-level check** queries `BookedProductVariant` directly and compares time windows for all existing bookings of the same product on the overlapping dates

If the combined quantity of overlapping bookings would exceed the available stock, the booking is rejected. Only after all checks pass does the system write the booking record and update the availability table.

---

## BookedProductVariant – Products Within a Booking

A single booking can contain multiple products. Each product is stored as a separate `BookedProductVariant` record linked to the booking. This model is the bridge between a booking and the product inventory system.

### Quantity and Amount

- The `quantity` field stores how many units of a product variant are included in the booking
- The `amount` field stores the **total price for that quantity**, not the per-unit price

For example, if a product costs ₹500 per day and the customer books 2 units, the stored amount is ₹1,000. This makes financial reporting and invoice generation straightforward without requiring per-unit calculations at query time.

### Return Tracking

Each `BookedProductVariant` record tracks its own return status independently:

- The `return_status` field is either `not_returned` or `returned`
- The `returned_at` field records the exact timestamp of when the item was returned
- This design enables **partial returns** — a booking can have some items returned while others remain active

### Extended Until

If a customer extends their rental for a specific product, the new return date is stored in `extended_until` on that product's `BookedProductVariant` record. This is important because:

- A booking may have different items extended to different dates
- Storing the extension at the item level rather than the booking level gives the system full flexibility
- The `ProductVariantAvailability` table is updated for the extension dates to keep inventory blocked

### Product Snapshot Data

When a `BookedProductVariant` is saved, the system automatically copies the following from the live product into backup fields:

- Product name
- Variant attribute
- Color
- Category
- Model name
- Thumbnail URL

This happens in the model's `save()` method. The reason for this design is **data integrity for historical records** — if a product is later edited or deleted from the catalog, every existing booking that included that product still has a complete, accurate record of what was rented. This is essential for:

- Invoice regeneration
- Audit trails
- Customer dispute resolution

Without these snapshot fields, historical invoices would show blank or incorrect product information.

### Measurements

For customized or tailored products, the `measurements` JSON field stores the customer's specific measurements such as:

- Collar size
- Chest size
- Shoulder width
- Sleeve length
- Waist, length, etc.

These measurements are recorded at the time of booking so the shop can prepare the correct product configuration for the customer.

### Database Indexes

The `BookedProductVariant` model defines indexes on:

- `(shop)` — For listing all booked items within a shop
- `(user)` — For finding booked items by the user who created them
- `(created_at)` — For chronological ordering

The `return_status` field is also independently indexed (`db_index=True`) for fast filtering between returned and not-returned items.

---

## Partial Return Feature

### Old Behavior vs. New Behavior

Previously, the system only supported full returns — all products in a booking had to be returned together before any inventory would be updated. This was a significant limitation because if a customer returned a shirt early but kept a jacket, the shirt's stock remained blocked even though it was physically back in the shop.

The partial return feature addresses this by allowing each `BookedProductVariant` to be returned individually.

### How Partial Return Works

As soon as a specific item is marked returned:

- The system marks that `BookedProductVariant` as `returned`
- Sets its `returned_at` timestamp
- Decrements the item's `ProductVariantAvailability` records
- The item is **immediately available** for new bookings

**Example:**

```
Booking contains: Suit, Shirt, Jacket

Day 1 → Shirt returned  → Shirt availability updated → Shirt bookable by others
Day 2 → Suit returned   → Suit availability updated  → Suit bookable by others
Day 3 → Jacket returned → Jacket availability updated → Jacket bookable by others

Booking stays active until all items returned + payments cleared + deposit settled
Then booking is marked Completed
```

---

## Booking Extensions

When a customer needs more time with a product:

- The new return date is stored in `extended_until` on the specific `BookedProductVariant`
- The system adds new `ProductVariantAvailability` records for the extension dates
- This ensures inventory remains blocked for the extended period
- No other customer can book the same product during that time

---

## Client Model

The `Client` model stores customer information for a shop. Each client belongs to a specific shop and is linked through a foreign key with `related_name="client_identities"`. The model uses `SafeDeleteModel` with `SOFT_DELETE`, so deleted clients are preserved in the database for historical reference.

### Phone Number Storage

Phone numbers are stored in two formats to maintain backward compatibility:

- **Legacy fields:** `client_phone_1` and `client_phone_2` as `BigIntegerField` values (older records stored as plain integers)
- **Current fields:** `phone_1_e164` and `phone_2_e164` using `PhoneNumberField` in **E.164 format** (e.g., `+919876543210`)

The E.164 format was introduced because the system integrates with WhatsApp, and WhatsApp's API requires the full international phone number including the country code. The default region for normalization is India (`region="IN"`), but the format supports international numbers. The `phone_1_e164` field has a database index for fast phone lookups.

### Uniqueness and Data Integrity

The model enforces a `unique_together` constraint on the combination of `(shop, client_name, phone_1_e164)`. This prevents duplicate client records within the same shop — if a client with the same name and phone number already exists in the shop, the system will reject the duplicate rather than creating another record.

Client names are automatically normalized on save. The model's `save()` method strips leading and trailing whitespace and applies Python's `title()` method, ensuring consistent capitalization across all records. For example, "john doe" becomes "John Doe".

### Database Indexes

The model defines several indexes for optimized queries:

- `(shop, client_name, client_phone_1)` — For searching clients by name and legacy phone within a shop
- `(shop, client_name, phone_1_e164)` — For searching clients by name and E.164 phone within a shop
- `(shop)` — For listing all clients in a shop
- `(client_phone_1)` — For phone number lookups across shops

### ClientV2 — Legacy Client Model

The system also contains a `ClientV2` model, which is the original client table from earlier versions of the application. This model stores `client_name`, `client_phone_1`, `client_phone_2`, and `client_address` as basic fields without E.164 phone support. The `BookingV2` model's `client` field still references `ClientV2` for backward compatibility, while the newer `client_identity` field references the `Client` model. Over time, the system is transitioning all client data to the `Client` model with proper phone number formatting.

---

## PaymentDetailsV2 – Payment Installments

The `PaymentDetailsV2` model stores individual payment transactions for a booking. Because customers frequently pay in installments — for example, an advance at the time of booking and the remainder at delivery — each payment is stored as a **separate record** rather than updating a single amount field.

Supported payment methods:

| Method | Label |
|--------|-------|
| `gpay` | GPay |
| `cash` | Cash |
| `upi` | UPI |

Each record stores the payment amount, method, and timestamp. The **sum of all `PaymentDetailsV2` records** for a booking represents the total amount collected so far, which can be compared against the booking's `total_amount` to determine the outstanding balance.

Database indexes are defined on `(booking)` and `(created_at)` for efficient payment lookups and chronological ordering.

---

## ClientRequirementV2 – Special Instructions

The `ClientRequirementV2` model stores custom instructions or special requests from the customer related to a booking. These might include:

- Color preferences
- Specific fitting instructions
- Event usage context
- Any other note the shop needs to remember when fulfilling the order

Storing these in a separate table keeps the main booking record clean and allows multiple requirements to be attached to a single booking if needed.

---

## BookingAdditionalCharge – Extra Charges

The `BookingAdditionalCharge` model stores extra costs that arise during a booking but are not part of the base rental price. Common examples include:

- Transportation charges
- Petrol costs
- Late return fees
- Service charges

Each charge has a name and amount linked to the booking. These charges are factored into the final bill presented to the customer.

The `amount` field is a `DecimalField` with a `MinValueValidator(0.01)`, ensuring no zero or negative charges can be recorded. The model defines composite indexes on `(booking, name)` and `(booking, amount)` for efficient querying and aggregation.

---

## Refund and Security Deposit Management

### RefundDetails

The `RefundDetails` model records refund transactions that occur when a booking is cancelled and money must be returned to the customer. Each refund record stores:

- The amount refunded (stored as a `DecimalField`)
- The method used (`cash`, `upi`, or `bank`)
- A reason for the refund (optional `TextField`)
- The staff member or user who processed the refund (`refunded_by` — FK to User, `SET_NULL`)
- The timestamp of when the refund was issued

Database indexes are defined on `(booking)`, `(refund_method)`, and `(created_at)` for efficient filtering and reporting.

Keeping a dedicated refund table ensures that cancellation handling is transparent and auditable.

### SecurityDepositPayment

The `SecurityDepositPayment` model records when a customer pays a security deposit. This is stored separately from regular payment records because:

- Security deposits are handled under different accounting rules
- They are liabilities that may be returned, not income
- Supported methods are `cash` and `upi`

The deposit `amount` is stored as a `DecimalField` for financial precision. Database indexes on `(booking)`, `(payment_method)`, and `(created_at)` support efficient deposit lookups and reporting.

### SecurityDepositAdjustment

The `SecurityDepositAdjustment` model handles what happens to the security deposit at the end of a booking. There are two possible adjustment types:

| Type | Meaning |
|------|---------|
| `refund` | Full or partial deposit returned to customer (no damage) |
| `deduction` | Amount kept by shop to cover damage or losses |

Each adjustment also stores a `payment_method` (`cash` or `upi`) for refund-type adjustments, and an optional `reason` text field to explain why the deduction or refund was made. Database indexes are defined on `(booking, adjustment_type)` and `(booking, created_at)` for efficient querying.

**Example:**

```
Security Deposit: Rs. 2,000
Damage assessed:  Rs. 500

System creates two records:
  Deduction: Rs. 500  (kept by shop)
  Refund:    Rs. 1,500 (returned to customer)
```

This gives a complete, transparent record of how the deposit was handled for both the shop and the customer.

### Security Deposit Lifecycle

```
1. Booking Created    → Customer pays deposit
2. Deposit Stored     → Recorded in SecurityDepositPayment
3. Booking Completed  → Product returned
4. Adjustment Made    → Full refund or partial deduction
5. Recorded           → Stored in SecurityDepositAdjustment
```

---

## Booking APIs

### Booking List APIs

Two separate list APIs exist because the needs of the web interface and the mobile app are quite different:

- **Desktop Booking List** — Supports advanced filtering, multi-field searching, and pagination designed for larger screens and keyboard/mouse interactions
- **Mobile Booking List** — Optimized for the Flutter app; returns smaller payloads with only the fields the mobile UI needs, resulting in faster responses and lower data usage on mobile networks

### Create Booking

Creating a booking is the most complex API in the module. It triggers:

1. Availability validation (date range check)
2. Time overlap check if times are specified
3. Creates the `BookingV2` record
4. Creates one `BookedProductVariant` record per product
5. Inserts or updates `ProductVariantAvailability` records for every date in the booking range

All of this happens within a **database transaction** to ensure consistency.

### Update Booking

Updating a booking re-runs the full availability validation whenever the dates, products, or quantities change:

- The old availability records are removed first
- New ones are calculated and inserted after validation passes
- The frontend sends the **complete list of product variants** during an update
- The backend processes additions, removals, and modifications in a single operation

### Update Booking Details

The `update-details` endpoint is a dedicated API for updating specific booking fields without modifying the product variants or availability. This endpoint is used when the shop needs to change information such as:

- Pickup date and return date
- Product variants (additions, removals, modifications)
- Quantities
- Other booking details

When updating fields that affect product availability — such as dates, products, or quantities — the system re-runs the full availability validation. It first removes the old availability records for the previous booking configuration, then rechecks availability for the new configuration, and only applies the changes if all validation passes. This atomic approach ensures that no availability gaps or double bookings can occur during the update process.

### Delete Booking

The delete logic depends on the booking's current state:

- **If completed or all products returned** — deletion proceeds without additional steps
- **If still active** — the system first decrements the `ProductVariantAvailability` records for all booked dates before deleting, ensuring the product stock returns to available for other customers

### Update Delivery Status

This API updates the `delivery_status` field on the booking. It is used by shop staff to mark when a product has been:

- Delivered to the customer
- Returned by the customer

For a return event, this API works alongside the partial return logic to update availability records for the specific returned item.

### Add and Delete Payment

- **Add Payment** — Appends a new `PaymentDetailsV2` record and updates the booking's `advance_amount`
- **Delete Payment** — Removes a payment record and decrements the paid amount accordingly

Both operations must maintain consistency between the individual payment records and the booking-level totals.

### Update Booking Status (Complete a Booking)

To transition a booking to **Completed**, the system checks:

- All `BookedProductVariant` records have `return_status = returned`
- Outstanding payment balance is zero
- Security deposit adjustments have been recorded

Only when all three conditions are true does the status update succeed.

### Booking Invoice

The invoice API supports two modes:

- **Send via WhatsApp** — Invoice sent directly to the customer's WhatsApp number (using E.164 phone number from the Client record)
- **Generate Invoice URL** — Returns a shareable URL that the frontend can display or forward

Invoice content is generated from the booking record, the booked product snapshots, and the payment history.

### Security Deposit APIs

| API | Description |
|-----|-------------|
| Add Security Deposit | Creates a `SecurityDepositPayment` record |
| Delete Security Deposit | Removes the deposit record |
| Add Adjustment | Creates a `SecurityDepositAdjustment` record (refund or deduction) |

These together provide a complete deposit lifecycle trail.

### Add and Delete Refund

- **Add Refund** — Records a refund transaction in `RefundDetails` when a booking is cancelled
- **Delete Refund** — Removes a refund record if entered incorrectly

---

## Client Card Operations

The system provides full CRUD APIs for managing client (customer) cards. These operations allow the shop to maintain a database of their customers.

### Create Client

Creates a new client record for the shop. The client card stores:

- Client name (automatically title-cased on save)
- Phone number (validated and stored in both legacy integer and E.164 format)
- Address and other contact details

The system validates the phone number format, normalizes it for E.164 storage, and checks uniqueness within the shop before saving.

### List and Retrieve Clients

The list API returns all clients for the current shop with support for search filtering by name and phone number. The retrieve API returns complete details for a specific client record.

### Update Client

Allows updating existing client details such as name, phone numbers, and address. Phone number changes go through the same validation and normalization process used during creation. The system checks that the updated phone number does not conflict with another existing client in the same shop.

### Delete Client

Soft-deletes the client record. Since the `Client` model uses `SafeDeleteModel`, the record is preserved in the database for historical reference. Bookings linked to the deleted client retain their client association.

---

## Dashboard Home API (Flutter App)

The dashboard API is a **single endpoint** that returns all the data the Flutter app's home screen needs in one call, avoiding multiple round trips.

The dashboard contains three sections:

**Upcoming Bookings**
- All bookings with pickup dates from today onward
- Helps staff prepare products for handover
- Ensures nothing is missed for the day

**Upcoming Returns**
- Active bookings where the pickup date has already passed but products have not yet been returned
- Gives staff visibility into which customers are expected to return items soon
- Only ongoing (not yet returned) bookings appear here

**Tailoring Pickups**
- If the shop has active tailor orders linked to bookings, those pickup schedules appear here alongside regular bookings
- This unified view means staff only need to check one screen to know everything that needs their attention that day
- Covers rental pickups, rental returns, and tailoring work pickups together

**Desktop Dashboard**

Starting from v4, a separate `BookingDesktopDashboardView` endpoint provides a dashboard optimized for the web/desktop interface. This returns more detailed data and different grouping compared to the mobile dashboard, taking advantage of the larger screen real estate available on desktop browsers.

---

## Ledger Integration

The booking module feeds data into the shop's ledger system. It provides:

- **Booking Income Entries** — Revenue from completed bookings
- **Payment Tracking** — Records of all payments received
- **Pending Payments Report** — Bookings where the advance paid is less than the total amount

This allows shop owners to:

- Generate financial summaries
- Track earnings by time period
- Identify customers with outstanding balances

---

## Background Tasks

The booking module uses several Celery background tasks for asynchronous operations that would be too slow or unreliable to perform during an API request.

### Document Upload Task

The `upload_booking_documents_task` handles asynchronous document uploads to S3. When documents are attached to a booking, the files are first saved to a temporary directory on the server, and the upload task is triggered in the background. The task:

- Reads each temporary file and uploads it to S3 under the path `bookings/{booking_id}/documents/{uuid}{ext}`
- Verifies each upload by performing an S3 `head_object` check after the upload completes
- Deletes the temporary file only after verification succeeds
- Updates the booking's `documents` list and `documents_details` progress tracking (status, total_count, uploaded_count)
- Retries up to 3 times with a 60-second delay on failure
- Cleans up orphaned temporary files on both success and failure

### Document Cleanup Task

The `cleanup_s3_documents_task` is triggered whenever booking documents need to be deleted from S3 — for example, when documents are removed during a booking update or when old documents are being purged. The task:

- Validates each URL's bucket host before attempting deletion
- Parses the S3 key from the URL path
- Deletes each object from S3 individually with error tracking
- Retries up to 3 times with a 60-second delay on failure
- Returns a detailed summary of deleted, failed, and total documents

### Thumbnail Generation Task

The `generate_booking_thumbnails_task` copies product thumbnail images to S3 for all booked items in a booking. When a booking is created, the booked product variants store the original product thumbnail URL. This task copies those images to S3 so that even if the original product image is changed or deleted, the booking retains its own copy of the thumbnail.

### Old Document Retention Cleanup

The `cleanup_old_booking_documents` task is a scheduled retention job that removes documents from completed bookings that are older than the configured retention period (default: 30 days). The process:

- Queries completed bookings where `completed_at` is older than the retention cutoff
- Processes bookings in batches of 100
- For each booking, extracts the document URLs, clears the `documents` field and updates `documents_details` with cleanup metadata
- Triggers the `cleanup_s3_documents_task` asynchronously for each batch of URLs
- Retries up to 3 times on failure

### Temporary File Cleanup

The `cleanup_old_temp_files` task runs daily via Celery Beat and removes temporary files from the `booking_docs` temp directory that are older than 24 hours. This prevents disk space accumulation from interrupted or failed upload processes.

---

## API Versioning

The booking module has evolved through multiple API versions, each building on the previous one:

| Version | Key Characteristics |
|---------|--------------------|
| **v1** | Original API using `JWTAuthentication`. Basic CRUD, dashboard, wallet endpoints. No client management. |
| **v2** | Added `ClientViewSet` for client CRUD. Added `BookingDetailAmountAPIView` for booking amount analysis. Included data migration endpoints for client addresses and shop booking IDs. |
| **v3** | Added `StandardResponseMixin` for consistent response formatting. Switched to `CutoffJWTAuthentication`. Added rate limiting via `ProductAvailabilityThrottle`. Enhanced product availability logic with time-overlap checks. |
| **v4** | Added `BookingDesktopDashboardView` for web interface dashboard. Enhanced booking list with desktop-optimized queries and response structure. |
| **v5** | Latest version. Added multipart document upload support (`MultiPartParser`, `FormParser`). Introduced `desktop-list` action for optimized desktop listing. Switched security amount tracking to `LedgerSecurityAmountAPIView`. Added document management within bookings (upload, remove). |

All versions from v3 onward use `CutoffJWTAuthentication` and the `StandardResponseMixin` that wraps responses in a consistent format with `status`, `message`, `data`, and `dev_message` fields. The v1 version uses plain `JWTAuthentication` without the standard response wrapper.

The v5 version represents the current state of the module and is used by both the Flutter mobile application and the desktop web interface.

---

## Model Summary

| Model | Purpose |
|-------|---------|
| `ClientV2` | Legacy customer contact information (plain integer phone numbers) |
| `Client` | Current customer model with E.164 phone numbers and uniqueness constraints |
| `BookingV2` | Master booking record — dates, amounts, status, documents |
| `BookedProductVariant` | Individual products within a booking, with return and extension tracking |
| `PaymentDetailsV2` | Individual payment installment records |
| `ClientRequirementV2` | Special customer instructions attached to a booking |
| `ProductVariantAvailability` | Daily per-date inventory availability tracking, prevents double bookings |
| `BookingAdditionalCharge` | Extra charges (transport, late fees, service) |
| `RefundDetails` | Refund transactions for cancelled bookings |
| `SecurityDepositPayment` | Security deposit collection records |
| `SecurityDepositAdjustment` | Security deposit refund or deduction at booking close |