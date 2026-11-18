# Tailored / Customization Module

---

## Overview

The **Tailored Module** manages custom clothing production, tailoring orders, measurements, materials, and payments within the platform. It allows shops to create custom products, track tailor work, manage customer measurements, and convert customized items into inventory products, rental bookings, or direct sales.

This module connects the **customization workflow** with three other parts of the system:

- **Inventory** — Completed tailor items can be added as new product variants to shop stock
- **Booking (Rental)** — Custom products can be linked directly into rental bookings with full payment history transfer
- **Sales** — Finished items can be sold directly to the customer

---

## Core Responsibilities

The Tailor module is responsible for:

- Managing tailors working with the shop
- Creating and managing customization orders
- Tracking tailoring work per product item
- Recording measurements for customized products
- Managing materials and fabric used for tailoring
- Tracking tailor payments
- Tracking customer payments in multiple installments
- Adding additional charges (urgent delivery, special work, etc.)
- Converting tailored products into inventory, bookings, or sales

---

## Main Workflow

A typical customization workflow follows these steps:

1. Shop creates a **Tailor Order** and selects the order type (shop inventory, rental, or sale)
2. **Tailor Order Items** are added — one per product being customized (e.g., Shirt, Pant, Kurta)
3. A **Tailor** is assigned to each item
4. Customer **Measurements** are recorded per item in JSON format
5. **Materials** used for each item are tracked with quantity and cost
6. Order and item **Status** are monitored as work progresses
7. **Customer Payments** are collected in installments
8. Once completed, the item is **converted** into one of:
   - An inventory product variant
   - A rental booking
   - A direct sale

---

## Order Types

A tailor order can belong to one of three categories. This determines the final destination of the completed product and which financial fields and dates are relevant.

| Type | Description |
|------|-------------|
| `shop` | Product created for the shop's own inventory |
| `rental` | Product created for a rental booking |
| `sale` | Product created for direct sale to the customer |

### Shop Inventory Orders

The shop creates a tailor order using its own raw material or fabric. The tailor creates the finished product. Once complete, the product is converted into a **product variant** and added to the shop's stock. These orders are primarily driven by the shop's operational needs rather than a specific customer request.

### Rental Customization Orders

A customer requests a customized rental product (for example, a custom-fit dress for an event). The shop collects the customer's measurements, assigns a tailor, and tracks the work. Once the product is ready, it is converted into a product variant and linked to a **rental booking**. Financial details — including paid amount, security deposit, and full payment history — are transferred into the booking at the time of conversion so no data is lost.

### Custom Sale Orders

A customer visits the shop and requests a custom clothing item for purchase. Measurements are collected, the tailor produces the item, and once completed, the product is delivered and recorded as a **sale**. These orders use a pickup date but typically do not require a return date.

---

## Tailor Model

The **Tailor** model stores information about tailors working with the shop.

### Key Fields

| Field | Description |
|-------|-------------|
| `shop` | The shop this tailor belongs to |
| `name` | Tailor's name |
| `phone` | Contact phone number (indexed for quick lookup) |
| `is_deleted` | Soft delete flag — deleted tailors are hidden but not removed |
| `created_at` | When the tailor record was created |

A shop can have multiple tailors. Each tailor can be assigned to specific order items, allowing the shop to distribute work across its tailor workforce. Because the model uses a soft delete (`is_deleted` flag), historical records showing which tailor did which work are never lost even after a tailor leaves the shop.

---

## TailorOrder – The Main Order Record

The **TailorOrder** model represents a single customization order. It holds the customer details, financial summary, dates, order status, and a reference to any booking created from this order.

### Customer Information

- `client` — Links to a customer record in the booking system (used for rental and shop orders)
- `client_phone_number` — Directly stored phone number (primarily used for sale orders where a full client record may not exist)
- `client_address` — Customer's address
- `staff` — The shop staff member handling the order

For **rental orders**, customer data is linked through the booking system's `Client` model. For **sale orders**, the phone number is stored directly in the order for simplicity.

### Financial Fields

The order stores a complete financial picture:

- `total_tailor_charges` — Total amount paid or owed to tailors
- `total_additional_charges` — Sum of all extra charges (urgent delivery, special work, etc.)
- `customer_total_charge` — Total amount the customer is expected to pay
- `paid_amount` — How much the customer has paid so far (updated as installments come in)
- `discount_amount` — Any discount applied to the order
- `security_amount` — Security deposit collected (relevant when the order will become a rental booking)

### Dates

The dates stored depend on the order type:

- **Rental orders** — Both `pickup_date` and `return_date` are used to define the rental window
- **Sale orders** — Only `pickup_date` is needed (the day the product is delivered to the customer)

### Order Status

| Status | Meaning |
|--------|---------|
| `initiated` | Order has been created, work not yet started |
| `in_progress` | Tailor has started working on the order |
| `completed` | All tailoring work is finished |

The `processed_to_booking_sale` field is a boolean flag that records whether the order has already been converted into a booking or sale. This prevents accidental double-conversion.

### Shop Tailor ID

Each tailor order is automatically assigned a **shop-specific unique ID** in the format `TO000001`, `TO000002`, etc. The generation logic mirrors the booking ID generation:

- The system acquires a database-level lock (`select_for_update`) on the shop record
- It reads the shop's `last_tailor_id` counter, increments it, and assigns the formatted value
- On first-time setup, it scans existing orders to find the current maximum ID

This ID is used for:
- Invoice tracking and references
- Customer communication (customers quote this ID when following up)
- Order tracking within the shop

### Conversion Tracking

The `created_booking` field stores a foreign key reference to the `BookingV2` record that was created when the tailor order was converted into a rental booking. This creates a direct, traceable link between the original customization order and the rental booking, making it easy to look up the full history of a product from its creation in the tailor module through its rental lifecycle.

---

## TailorOrderItem – Individual Products in an Order

A single TailorOrder may contain multiple customized products. Each product is stored as a separate **TailorOrderItem** record. For example, a customer order for a Pant, Shirt, and Kurta would create three TailorOrderItem records all linked to the same parent TailorOrder.

This structure allows the shop to:
- Assign different tailors to different items in the same order
- Track the completion status of each item independently
- Set separate expected and actual delivery dates per item
- Record item-specific measurements and charges

### Key Fields

| Field | Description |
|-------|-------------|
| `order` | The parent TailorOrder this item belongs to |
| `product_name` | Name of the product being made (e.g., "Black Shirt", "Jogger Pants") |
| `product_type` | Category of product (e.g., Shirt, Pant) |
| `quantity` | Number of units to produce (minimum 1) |
| `reference_image` | URL of an example design image provided by the customer |

### Tailor Assignment

Each item can be assigned to a specific tailor:

| Field | Description |
|-------|-------------|
| `assigned_tailor` | The tailor responsible for this item |
| `tailor_charge` | Amount to be paid to the tailor for this item |
| `customer_tailoring_charge` | Amount charged to the customer for the tailoring service |
| `customer_item_charge` | Final total price charged to the customer for this item |

The separation between `tailor_charge` and `customer_tailoring_charge` allows the shop to maintain its own margin on tailoring costs without exposing the tailor's rate to the customer.

### Item Status

| Status | Meaning |
|--------|---------|
| `pending` | Item created, tailor not yet started |
| `stitching` | Tailor is actively working on the item |
| `ready` | Item is completed and ready for delivery |
| `delivered` | Item has been handed to the customer |
| `cancelled` | Item has been cancelled |

Each item progresses through these statuses independently, meaning one item in an order can be `ready` while another is still `stitching`.

### Measurements

Measurements are stored as a **flexible JSON field**, allowing any measurement format to be recorded:

```json
{
  "chest": 40,
  "waist": 34,
  "shoulder": 18,
  "length": 28,
  "collar": 15
}
```

This flexibility is important because different product types require different measurements — a shirt needs collar and chest measurements, while trousers need waist and inseam. Using JSON rather than fixed columns allows the system to handle all product types without schema changes.

### Delivery Tracking

- `expected_completion_date` — When the tailor is expected to finish the item
- `actual_completion_date` — When the item was actually completed

Comparing these two dates helps the shop monitor tailor performance and identify delays before they affect the customer.

### Product Conversion Tracking

The `created_product_variant` field stores a reference to the `ProductVariant` that was created when this tailor order item was converted into an inventory product. Once this field is populated, the item has an existence in the main product catalog and can be added to bookings or sales. This field is the bridge between the tailoring workflow and the inventory/booking system.

---

## TailorMaterialUsage – Fabric and Material Tracking

The **TailorMaterialUsage** model tracks materials and fabrics used for each tailor order item. For each material used, the system records:

| Field | Description |
|-------|-------------|
| `order_item` | The TailorOrderItem this material was used for |
| `material` | The product variant representing the fabric or material |
| `quantity_meters` | How many meters of material were used |
| `total_amount` | Cost of the material used |

**Example:**

| Material | Quantity | Cost |
|----------|----------|------|
| Cotton Fabric | 2.5 meters | Rs. 250 |
| Silk Lining | 1.2 meters | Rs. 180 |

This tracking helps the shop:
- Calculate the true cost of each customized item
- Monitor fabric inventory consumption
- Generate accurate material cost reports
- Price future orders more accurately

---

## TailorPayment – Payments to Tailors

The **TailorPayment** model tracks payments made directly to tailors for their work.

### Key Fields

| Field | Description |
|-------|-------------|
| `tailor` | The tailor receiving the payment |
| `order_item` | The specific order item the payment relates to |
| `amount_paid` | Amount paid |
| `payment_method` | `cash` or `upi` |
| `notes` | Optional notes about the payment |

> **Note:** This model currently exists in the system but is **not actively used**. Tailor payment tracking is planned for future activation. The model is in place so the feature can be enabled without requiring schema changes.

---

## TailorOrderAdditionalCharge – Extra Charges

The **TailorOrderAdditionalCharge** model stores any extra costs that arise beyond the standard tailoring charge. These are charges the shop applies to the customer for special requests or services.

**Examples of additional charges:**

- Urgent delivery (priority stitching)
- Special embroidery or design work
- Custom buttons or accessories
- Express finishing

### Key Fields

| Field | Description |
|-------|-------------|
| `order` | The TailorOrder this charge belongs to |
| `name` | Description of the charge (e.g., "Urgent Stitching") |
| `amount` | Charge amount (minimum Rs. 0.01) |

These charges feed into the `total_additional_charges` field on the parent TailorOrder, contributing to the customer's final bill.

---

## CustomerPayment – Customer Payment Installments

The **CustomerPayment** model tracks payments made by the customer for a tailor order. Because customers often pay in installments — for example, an advance when placing the order and the balance on delivery — each payment is stored as a separate record.

### Key Fields

| Field | Description |
|-------|-------------|
| `order` | The TailorOrder this payment belongs to |
| `payment_amount` | Amount paid in this installment |
| `payment_method` | `cash` or `upi` |
| `created_at` | Timestamp of when the payment was made |

**Example payment history for one order:**

| Payment | Amount |
|---------|--------|
| Advance at order placement | Rs. 1,000 |
| Partial payment mid-order | Rs. 500 |
| Final balance on delivery | Rs. 2,000 |

The sum of all `CustomerPayment` records for a TailorOrder is compared against `customer_total_charge` to determine how much is still outstanding.

---

## Conversion Workflows

One of the most important features of the tailor module is the ability to **convert a completed tailor order into a product, a booking, or a sale**. This is what connects the customization module to the rest of the system.

### Conversion to Inventory Product

Used when the tailor order type is `shop`:

1. Tailor order is created for shop inventory
2. Tailor completes the product
3. The completed item is converted into a **ProductVariant**
4. The product variant is added to the shop's inventory catalog
5. The `created_product_variant` field on the TailorOrderItem is updated with the new product reference

Once in inventory, the product can be rented, sold, or kept in stock for future orders.

### Conversion to Rental Booking

Used when the tailor order type is `rental`. This is the most complex conversion because it involves migrating financial data:

1. Customer requests a custom rental product
2. TailorOrder is created with rental dates (pickup and return)
3. Measurements are collected and stored
4. Tailor creates the product
5. The item is converted into a **ProductVariant**
6. A **BookingV2** record is created with the correct dates, shop, and client
7. The following financial data is **automatically transferred** from the TailorOrder into the Booking:
   - Paid amount (so the customer's advance is not lost)
   - Security amount
   - All `CustomerPayment` records are migrated into `PaymentDetailsV2` records on the new booking
8. The `created_booking` field on the TailorOrder is updated with the new booking reference
9. The `processed_to_booking_sale` flag is set to `True` to prevent double conversion

This seamless transfer ensures the customer's payments and deposit are carried over correctly without manual re-entry.

### Conversion to Sale

Used when the tailor order type is `sale`:

1. Customer requests a custom clothing item for purchase
2. TailorOrder is created (no return date required)
3. Measurements are collected
4. Tailor creates the product
5. Product is delivered to the customer
6. Sale is recorded in the system

---

## Tailor APIs

### Tailor CRUD

Standard operations for managing tailor records:

- **Create Tailor** — Register a new tailor with the shop
- **Update Tailor** — Modify tailor name or contact details
- **Delete Tailor** — Soft-delete a tailor record
- **List Tailors** — View all active tailors for the shop
- **Retrieve Tailor** — Get full details of a specific tailor

### Tailor Order CRUD

Operations for managing customization orders:

- **Create Tailor Order** — Start a new customization order, set order type, customer, dates
- **Update Tailor Order** — Modify order details, dates, pricing, notes
- **Delete Tailor Order** — Remove an order that is no longer needed
- **List Tailor Orders** — View all orders with filtering and search
- **Retrieve Tailor Order** — Get full order details including items, payments, and charges

### Tailor Order Item Operations

- **Add Item** — Add a new product to a tailor order
- **Update Item** — Change item details, status, tailor assignment, or measurements
- **Delete Item** — Remove an item from the order
- **Assign Tailor** — Link a tailor to a specific order item
- **Update Status** — Move an item through its status stages (pending → stitching → ready → delivered)

### Conversion APIs

These APIs execute the conversion logic that connects the tailor module to the rest of the system:

**Convert Order to Product (Inventory)**
- Validates that the order is completed and not yet converted
- Creates a new `ProductVariant` in the product catalog
- Updates `created_product_variant` on the TailorOrderItem
- Sets `processed_to_booking_sale` to `True`

**Convert Order to Booking (Rental)**
- Validates order completion and that conversion has not already happened
- Creates a `ProductVariant` from the tailor item
- Creates a `BookingV2` with the correct dates, shop, and client
- Migrates all `CustomerPayment` records into `PaymentDetailsV2` records on the new booking
- Copies security amount into the booking
- Links the booking back to the TailorOrder via `created_booking`
- Sets `processed_to_booking_sale` to `True`

> **Note:** Invoice sending functionality exists in the module but is **not currently active**.

---

## Model Summary

| Model | Purpose |
|-------|---------|
| `Tailor` | Tailor information — name, phone, shop, soft delete |
| `TailorOrder` | Main customization order — type, customer, pricing, status, dates |
| `TailorOrderItem` | Individual products within an order — tailor, measurements, status, charges |
| `TailorMaterialUsage` | Materials and fabric consumed per order item |
| `TailorPayment` | Payments made to tailors (model exists, not yet actively used) |
| `TailorOrderAdditionalCharge` | Extra charges applied to an order (urgent delivery, special work) |
| `CustomerPayment` | Customer payment installments for a tailor order |