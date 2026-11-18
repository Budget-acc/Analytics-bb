# BookieBuddy Backend Overview

## Application Outcome

BookieBuddy is system designed to manage rental operations, direct product sales, and customization-based orders for shop-based businesses. The backend provides the core logic required to manage inventory, track financial activity, process orders, and support multi-shop management with role-based access control.

The system is built to support shops that operate using three primary product flows:

- Rental bookings
- Direct sales
- Customization-based product creation

The backend coordinates inventory management, order lifecycle handling, financial tracking through a unified ledger, and operational monitoring across multiple shop locations.

The goal of the backend is to ensure that all business activities — including bookings, sales, customization works, expenses, and stock updates — are consistently tracked and synchronized within the system.

---

# Core Backend Modules

## 1. Bookings (Rental Management)

The Bookings module manages the rental lifecycle of products.

Each booking is created for a specific time period defined by:

- Pickup Date
- Return Date

During the booking period, the rented product is considered **out of inventory** and unavailable for other bookings. Once the return date is reached and the item is returned, the product becomes available again in stock.

### Cooling Period

In addition to the booking duration, the system supports a **Cooling Period** which affects inventory availability.

The cooling period represents the time required either before or after a booking to prepare or process the item.

This period can vary depending on the shop and may occur in two forms:

**Pre-Pickup Cooling Period**

A buffer period before the pickup date. This time can be used for preparation tasks such as packaging, inspection, or item preparation.

**Post-Return Cooling Period**

A buffer period after the return date. This allows time for processes such as washing, repair, inspection, or rest before the item becomes available again for booking.

Because of the cooling period, the actual time during which the item is unavailable for other bookings may extend beyond the pickup and return dates.

### Backend Responsibilities

- Managing booking records
- Tracking pickup and return dates
- Applying cooling period rules for inventory availability
- Temporarily locking stock during the booking and cooling duration
- Restoring stock availability only after the complete availability window has ended

---

## 2. Sales

The Sales module manages direct product purchases.

When a sale occurs, the product is permanently removed from the active stock. Unlike bookings, the item does not return to inventory.

Key backend responsibilities:

- Recording product sales
- Updating inventory after sale
- Maintaining transaction records for financial tracking

---

## 3. Customization Works

Customization Works handle orders where products are created or modified based on specific requirements.

This module supports two types of customization workflows.

### Customer-Driven Customization

Customers can request custom products based on measurements or special requirements. These customized items can be created for:

- Rental usage
- Direct purchase

Once the customization work is completed, the order transitions into either the **Booking flow** or the **Sales flow**.

### Shop-Driven Customization

Shops can create customized items internally and add them directly to their inventory.

These items can then become part of:

- Rental inventory
- Sales inventory

Key backend responsibilities:

- Managing customization orders
- Tracking work progress
- Converting completed customization works into sales or bookings
- Supporting inventory additions from customization output

---

## 4. Ledger and Expense Management

The Ledger serves as the central financial record for the system.

All financial activities are recorded in the ledger to maintain a consistent audit trail.

These activities include:

- Income from sales
- Income from rental bookings
- Income from customization works
- Operational expenses

### Expense Management

Expenses can be added separately and are automatically reflected in the ledger.

Key backend responsibilities:

- Recording all financial transactions
- Maintaining income and expense tracking
- Providing a unified view of cash flow

---

## 5. Stock Management

Stock Management controls the entire inventory structure within the system.

The inventory hierarchy includes:

- Services
- Sub-services (General Services)
- Products
- Variants

All inventory items are associated with specific shop locations.

Key backend responsibilities:

- Creating and maintaining products
- Managing product variants
- Updating stock levels
- Linking inventory items to shops

---

## 6. Shop Transfer History

The system maintains records of transfers between shop entities.

Transfer history allows tracking of inventory or operational changes across shop locations.

Key backend responsibilities:

- Recording shop-level transfers
- Maintaining transfer history for operational tracking

---

## 7. Shop Performance Tracking

The backend provides mechanisms to track the operational performance of shops.

Performance tracking is based on business activities such as:

- bookings
- sales
- customization works
- financial activity recorded in the ledger

This allows monitoring the overall health and productivity of shop operations.

---

# Role-Based Access Control

The system operates within a shop-based structure with a role hierarchy.

## Owner

Owners have full access across the system.

Capabilities include:

- Access to multiple shops
- Full visibility of financial data
- Configuration of shop settings
- Ability to define restrictions for staff members

---

## Manager

Managers can operate across multiple assigned shops.

Capabilities include:

- Managing shop operations
- Accessing shop performance data

---

## Staff

Staff members have restricted access.

Capabilities include:

- Access limited to a single shop
- Permissions controlled by the Owner

Owners can configure restrictions to control what staff members can view or modify.

---

# Core Backend Business Rules

## Stock Synchronization

Stock updates follow specific rules based on the transaction type.

Sales immediately reduce the available stock.

Bookings temporarily lock stock for a defined rental period and release it back into inventory after return.

---

## Unified Financial Tracking

Every financial activity in the system must be reflected in the Ledger.

This includes:

- Sales income
- Rental income
- Expenses

---

## Multi-Shop Support

The backend supports operations across multiple shop locations.

Owners and Managers can manage multiple shops, while Staff members operate within a single shop environment.

# Backend Applications

The documentation for each Django application used in the BookieBuddy backend.

## Applications

- authentication
- booking_v2
- sales
- expenses_v2
- tailored
- services
- shop
- notifications
- pdf_service
- subscriptions
- whatsapp_integration