# Shop Module – Rental Application

---

## Overview

The **Shop Module** is the foundational module of the entire rental application. Every other module — bookings, sales, services, expenses, notifications, tailoring — operates within the context of a shop. The Shop module manages the core structure of shop entities, user membership and roles, feature access policies, operational settings, staff records, activity tracking, and bug reporting.

A shop represents an independent business unit that performs bookings, sales, and customization works. Each shop operates with its own settings, policies, staff members, and operational records. The system is designed so that all data is scoped per shop, ensuring that no data from one shop leaks into another.

When a new shop is created from the Shop Application, a user is also created at the same time. The shop and the user are linked together because user roles within the system depend on the shop context. This means the system always creates a user–shop pair during onboarding — the user becomes the Owner of that shop, and the `ShopUser` record ties them together with the appropriate role.

The module connects directly with:

- **Authentication** — User creation, phone normalization, JWT authentication
- **Subscriptions** — Subscription plans, status tracking, feature gating
- **Booking** — Booking creation relies on shop settings (cooling period, invoice IDs)
- **Sales** — Sale records are scoped per shop
- **Services** — Products and services belong to shops
- **Expenses** — Expense tracking per shop
- **Tailoring** — Tailor orders and customer payments per shop
- **Notifications** — Bug report email alerts via Celery
- **S3** — Image uploads for shop logos and bug report images

---

## Shop – The Core Business Entity

The `Shop` model is the central table of the entire system. Every business entity is stored here. It holds information about the shop's identity, contact details, subscription status, operational limits, invoice numbering, and activation state. All other modules reference the shop through foreign keys.

### Basic Shop Information

The shop stores general information required to identify and contact the business:

- `name` — The name of the shop (max 200 characters)
- `phone` — Primary phone number
- `phone2` — Secondary phone number
- `address` — Street address stored as a text field
- `email` — Shop email address
- `city`, `state`, `place`, `pincode` — Location details for the shop
- `gst_number` — GST registration number for tax purposes
- `img` — A URL pointing to the shop's logo or image stored on S3
- `created_at` — Timestamp when the shop was created

These fields store the basic identity and contact details of the shop. The `img` field is a `CharField` (not a file field) because images are uploaded to S3 and only the resulting URL is stored in the database. When the shop is created or updated, the image file is processed — converted to PNG format, resized if necessary — and uploaded to S3 under a path like `{shop_name}/shop_images/{uuid}.png`. The maximum allowed image size is 3MB.

### Database Uniqueness

The `Shop` model enforces a case-insensitive uniqueness constraint on the combination of `name` and `place`. This is implemented using Django's `UniqueConstraint` with the `Lower` function:

- Two shops cannot have the same name in the same place, regardless of letter casing
- For example, if "Royal Rentals" exists in "Mumbai", attempting to create "royal rentals" in "mumbai" will be rejected
- This constraint is named `unique_shop_name_place_ci` in the database

### Soft Delete

The `Shop` model extends `SafeDeleteModel` with the `SOFT_DELETE` policy. When a shop is deleted, it is not physically removed from the database. Instead, a `deleted` timestamp is set. This preserves all historical data — bookings, sales, payments — that reference the shop. The admin panel provides the ability to hard-delete shops when permanent removal is necessary.

---

### Shop Security

The shop contains a `password` field that is used for accessing sensitive features such as ledger-related data, monthly gross reports, and other protected operations. This is not the user's login password — it is a shop-level password that the Owner sets to gate access to certain features.

- The default password is `"600600"` — this is set automatically when a new shop is created
- The `password_changed_at` field tracks when this password was last changed
- When a feature policy requires a password, the system checks whether the role of the requesting user falls within the configured password requirement, and if so, prompts for this shop password

This shop-level password is separate from user authentication. It acts as a second layer of protection for high-sensitivity operations within the shop.

---

### Subscription Management

Each shop has a subscription status that determines whether it can continue using the system. The subscription fields stored directly on the shop model include:

- `subscription_status` — The current status of the shop's subscription, which can be one of:
  - `ACTIVE` — The shop has a valid subscription
  - `EXPIRED` — The subscription has lapsed
  - `NONE` — The shop has never subscribed
- `subscribed_date` — The date when the shop first subscribed
- `renewal_due_date` — The date by which the subscription should be renewed
- `subscription_renewal_price` — The renewal price of the subscription at the time it was purchased, stored as a decimal field so it can be referred to later during renewal

When a subscription is purchased, the renewal price is captured and stored on the shop so the system can display it during future renewal flows without needing to look up the original plan. The `SubscriptionActivePermission` class is used across many client-side API endpoints to ensure that only shops with active subscriptions can access premium features.

The v1 admin serializer also integrates with the `ShopSubscription` model from the subscriptions module. When listing shops in the admin panel, the serializer first checks if a `ShopSubscription` record exists for the shop. If it does, the subscription status is pulled from that record (which may be more up-to-date). If not, it falls back to the `subscription_status` field stored directly on the shop.

---

### Shop Limits

The system enforces limits on how many resources a shop can use. These limits are tied to the subscription plan but can be extended through additional fields on the shop.

**Stock Limit**

Each shop has a base stock limit defined by the subscription plan. The `extra_stock_limit` field (a `PositiveIntegerField` defaulting to 0) allows additional product stock capacity beyond the base plan. For example, if the plan allows 100 products and the shop needs 150, the admin can set `extra_stock_limit` to 50.

**User Limit**

A shop normally allows a fixed number of users — the base limit is **three users**. These three users can be any combination of Owners, Managers, or Staff. If the shop needs to add more users beyond this base limit, the `extra_user_limit` field is used. For example, if a shop needs five users instead of three, the admin sets `extra_user_limit` to 2. The system calculates the effective limit as:

```
effective_limit = BASE_USER_LIMIT_IN_SHOP + shop.extra_user_limit
```

Where `BASE_USER_LIMIT_IN_SHOP` is a constant set to 3. Before linking a new user to a shop, the system counts the current active `ShopUser` records and rejects the link if the limit would be exceeded.

---

### Invoice Number Management

Some shops may have already used manual invoice numbering before adopting the system. To continue their numbering correctly without gaps or resets, the system allows defining custom starting numbers.

The following **starting ID** fields are used only during initial setup:

- `booking_start_id` — The starting invoice number for bookings
- `sale_start_id` — The starting invoice number for sales
- `tailor_start_id` — The starting invoice number for tailor orders

These values are used **only when there is no previous record** — that is, when the `last_booking_id`, `last_sale_id`, or `last_tailor_id` fields are still at zero. Once the first booking, sale, or tailor order is created using the starting ID, the system switches to using the "last ID" fields for all subsequent records.

The system also tracks the most recently used IDs:

- `last_booking_id` — The last generated booking invoice number
- `last_sale_id` — The last generated sale invoice number
- `last_tailor_id` — The last generated tailor order invoice number

When a new record is created, the system acquires a **database-level lock** (`select_for_update`) on the shop record, reads the corresponding "last ID" field, increments it by one, saves it back to the shop, and assigns the formatted value to the new record. This locking mechanism prevents two simultaneous records from receiving the same ID even under high concurrency.

For example, if `last_booking_id` is 5, the next booking will receive ID 6. If `last_booking_id` is 0 and `booking_start_id` is 100, the first booking will receive ID 100, and `last_booking_id` will be updated to 100.

The v1 admin serializer includes validation for these fields:

- `booking_start_id` must be a positive integer
- `sale_start_id` must be a positive integer
- Changing `booking_start_id` is blocked if the shop already has bookings
- Changing `sale_start_id` is blocked if the shop already has sales

---

### Terms and Conditions

The `terms_and_conditions` field is a `JSONField` that stores a list of bullet points for the shop's terms and conditions. These are displayed on invoices and other customer-facing documents. The field accepts a JSON array of strings, and when submitted as a string through multipart form data, the system parses it using `json.loads`.

### Activation State

The `is_active` field is a boolean that determines whether the shop is operational. An inactive shop cannot be accessed by its users through the client-side API. The `get_active_shop_id` utility function checks both `is_active` and the `deleted` field before allowing access.

---

## ShopUser – User Membership and Roles

The `ShopUser` model defines the many-to-many relationship between a **User** and a **Shop**. This is the model that determines what role a particular user has within a specific shop.

A user can belong to multiple shops, and their role can differ in each. For example, the same person could be an Owner in one shop and a Manager in another. The model stores:

- `shop` — Foreign key to the Shop (cascading delete)
- `user` — Foreign key to the User from the authentication module (cascading delete)
- `role` — The user's role within that shop, one of:
  - `OWNER` — Full control over the shop, including settings, policies, and staff management
  - `MANAGER` — Elevated access with management capabilities
  - `STAFF` — Basic operational access for day-to-day tasks
- `is_active` — Whether this user–shop link is currently active
- `created_at` — When the link was created

The model enforces a `unique_together` constraint on `(shop, user)`, meaning a user can only be linked to a specific shop once. If the link needs to be modified (for example, changing the role), the existing record is updated rather than creating a new one.

### Role Limits per Shop

The system enforces role-based limits when linking users to shops:

- Only **1 Owner** is allowed per shop
- Only **1 Manager** is allowed per shop
- Up to **2 Staff** members are allowed per shop

These limits are enforced in the `ShopUserLinkAPIView` during the POST and PATCH operations, in addition to the overall user count limit (base 3 + extra).

### Soft Delete

The `ShopUser` model also uses `SafeDeleteModel` with `SOFT_DELETE`. When a user–shop link is deactivated, the record is soft-deleted rather than permanently removed. The v1 API also supports hard deletion of the `ShopUser` relationship when needed.

---

## ShopFeaturePolicy – Feature Access Control

Each shop can configure feature access policies that control which users can access specific functionality within the application. These policies are set by the **Owner** of the shop and provide a granular permission system on top of the role-based access.

### Feature Keys

Features are defined as a set of predefined choices using the `FeatureKey` enum:

| Feature Key | Description |
|-------------|-------------|
| `LEDGER_VIEW` | Viewing the shop's financial ledger |
| `MONTHLY_GROSS_VIEW` | Viewing monthly gross revenue reports |
| `BOOKING_DELETE` | Deleting bookings |
| `BOOKING_EDIT` | Editing existing bookings |
| `BOOKING_PAYMENT` | Managing booking payments |
| `TRANSFER_PRODUCT` | Transferring products between shops |
| `PRODUCT_DELETION` | Deleting products from inventory |
| `PRODUCT_EDIT` | Editing product details |

### Password Requirements

Each feature policy defines **who must enter the shop password** before accessing the feature. The `PasswordRequirement` enum provides four levels of access control:

| Requirement | Meaning |
|-------------|---------|
| `ALL` | Everyone must enter the password — including the Owner |
| `NONE` | No password is required for anyone |
| `STAFF` | Only Staff members must enter the password |
| `MANAGER_AND_STAFF` | Both Managers and Staff must enter the password (default) |

These follow a hierarchical order. The default is `MANAGER_AND_STAFF`, which means Owners can access the feature freely, but Managers and Staff need to provide the shop password.

### How Policies Are Stored

Each policy is stored as a record linking a shop to a feature with a password requirement:

- The `unique_together` constraint on `(shop, feature)` ensures only one policy exists per feature per shop
- An index on `(shop, feature)` optimizes lookups when the system checks access during API calls
- The `is_active` field allows policies to be deactivated without deletion
- `created_at` and `updated_at` timestamps track policy lifecycle

### How Policies Are Updated

The `ShopPrivacySettingsView` allows the Owner to update multiple feature policies in a single PATCH request. The request body is a dictionary where keys are feature names and values are password requirement levels. For each entry, the system uses `update_or_create` to either update the existing policy or create a new one. This makes it efficient to configure all policies at once from the settings screen.

---

## ShopSettings – Operational Configuration

The `ShopSettings` model stores configurable operational settings for a shop. It is connected to the shop via a `OneToOneField`, meaning each shop has exactly one settings record. The settings determine how certain system behaviors function across the application.

### Cooling Period

The cooling period is a critical setting that affects booking availability. It determines how long a product remains unavailable after a booking return or before a pickup. This gives shops time to clean, inspect, repair, and prepare products between rentals.

The cooling period configuration includes two fields:

- `cooling_days` — The number of days for the cooling period (default: 0, meaning no cooling period)
- `cooling_period_type` — Whether the cooling period is applied:
  - `"after"` — After the return date (default). The product is blocked for the specified number of days after the customer returns it.
  - `"before"` — Before the pickup date. The product is blocked for the specified number of days before the next customer picks it up.

These settings are read by the booking module when checking product availability and calculating `cooling_period_end` dates on bookings.

### Default Action Button

The frontend application contains an action button (a floating action button or primary create button) that is used to create new operations. The `default_action` setting controls what happens when this button is pressed:

| Value | Behavior |
|-------|----------|
| `ASK_EVERY_TIME` | The app asks the user which type of record to create (default) |
| `BOOKINGS` | Directly opens the booking creation flow |
| `SALES` | Directly opens the sale creation flow |
| `CUSTOMIZATION` | Directly opens the customization/tailor work creation flow |

This setting helps shops that primarily do one type of operation (for example, a shop that does 90% bookings) to skip the selection step and go directly to their most common workflow.

### Client Search Behavior

The `search_client` boolean field controls how clients are handled when creating or updating records. When set to `true`, the system searches for existing clients rather than creating new ones. When set to `false`, the system defaults to creating a new client. This setting exists because searching by client ID can cause confusion during booking creation — some shops prefer to always create a fresh client entry, while others want to reuse existing client records.

### Settings Creation

The settings are created or updated through the `ShopSettingsViewSet` using a PATCH endpoint. The system uses `get_or_create` to ensure a settings record always exists — if one does not exist when an update is attempted, a new record is created with sensible defaults (0 cooling days, ASK_EVERY_TIME action, no client search, "after" cooling period type). The endpoint validates each field before saving:

- `cooling_days` must be a non-negative integer
- `default_action` must be one of the four valid choices
- `search_client` must be a boolean
- `cooling_period_type` must be either `"after"` or `"before"`

---

## Staff – Shop Personnel

The `Staff` model stores basic information about staff members associated with a shop. Staff records are separate from `User` accounts — a staff member represents someone who works at the shop and is assigned to bookings, sales, and customization work, but does not necessarily have a system login.

Each staff record includes:

- `shop` — Foreign key to the shop (cascading delete)
- `name` — The staff member's name (max 100 characters, validated to be at least 2 characters, cleaned of extra whitespace)
- `phone` — Phone number (indexed for fast lookup, validated and normalized to 10 digits, unique per shop)
- `color` — A hex color code (max 8 characters, indexed) used to visually identify the staff member in the UI
- `created_at` — When the staff record was created
- `is_deleted` — Soft delete flag (boolean, not using `SafeDeleteModel`)

The `color` field is particularly important for the frontend. When bookings, sales, and customization work records are listed, the staff member's color is displayed alongside their name, making it easy to visually scan and identify who handled which operation. The system enforces color uniqueness per shop — two staff members in the same shop cannot have the same color. This is checked inside a database transaction using `select_for_update` to prevent race conditions.

### Phone Number Validation

Staff phone numbers go through a normalization process using the `validate_and_normalize_phone` utility:

- Country codes like `+91` or `91` are stripped
- Leading zeros are removed
- The result must be exactly 10 digits and not start with 0
- The phone must be unique within the shop (the serializer checks for duplicates excluding the current record during updates)

### Staff Soft Deletion and Restoration

When a staff member is deleted, the `is_deleted` flag is set to `true` rather than actually removing the record. This preserves the staff member's association with historical bookings, sales, and other records. The staff list endpoint supports filtering by the `is_deleted` parameter to show either active or deleted staff.

Deleted staff members can also be **restored** using the `restore` action endpoint. This sets `is_deleted` back to `false`, reactivating the staff member. Both deletion and restoration use `select_for_update` to prevent concurrent modification issues.

### Database Indexes

The Staff model defines composite indexes for optimized queries:

- `(shop, name)` — For listing staff within a shop
- `(phone,)` — For phone number lookups

---

## ShopActivityLog – Activity Tracking

The `ShopActivityLog` model records important activities occurring within a shop. The primary purpose of this model is to provide transparency — the Owner can see exactly what actions were performed, by whom, and on which objects.

Each activity log entry stores:

- `shop` — The shop where the activity occurred
- `user` — The user who performed the action (set to `SET_NULL` so logs are preserved even if the user is deleted)
- `content_type` — A `GenericForeignKey` content type indicating which model the action was performed on (for example, BookingV2, Sale, ProductVariant)
- `object_id` — The primary key of the affected object
- `content_object` — The `GenericForeignKey` that resolves to the actual Django model instance
- `action` — A string describing the type of action, such as `"created"`, `"updated"`, `"deleted"`, `"status_changed"`, `"variant_deleted"`
- `log_message` — A human-readable message describing the event
- `created_at` — When the action occurred

### What Gets Tracked

The activity log captures a wide range of operations:

- Creating bookings, sales, and customization works
- Updating booking details, payment statuses, delivery statuses
- Adding or deleting payments
- Deleting or modifying records
- Changing statuses on bookings or orders
- Deleting product variants

This tracking helps the Owner monitor operations such as:

- Which user deleted a payment from a booking
- When a booking status was changed and by whom
- Whether a staff record was deleted or modified
- What product variants were removed from inventory

### Activity Log API

The `ShopActivityLogViewSet` provides a paginated, filterable list endpoint. The supported filters are:

- `q` — Free text search against the log message
- `action` — Filter by specific action type (exact match)
- `user` — Filter by user name or phone number
- `content_type` — Filter by model type (for example, `booking_v2.bookingv2` or just `bookingv2`)
- `start_date` / `end_date` — Filter by date range (format: `YYYY-MM-DD`)

The API includes special handling for `variant_deleted` actions. When a product variant is deleted, the system fetches the variant's `unique_code` in bulk (using `all_objects` to include soft-deleted records) and includes it in the response. This allows the UI to display which specific variant was deleted, even after the variant itself has been soft-deleted from the main table.

### Database Indexes

The model defines indexes on:

- `shop` — For filtering logs by shop
- `action` — For filtering by action type
- `created_at` — For date-based ordering and filtering

Records are ordered by `-created_at` (newest first) by default.

---

## BugReport – Issue Reporting

The `BugReport` model allows users within a shop to report system issues directly from the application. Each bug report links to a shop and a reporter (user), and includes structured information about the problem.

### Fields

- `shop` — The shop where the bug was reported (cascading delete)
- `reporter` — The user who reported the bug (`SET_NULL` to preserve the report if the user is deleted)
- `title` — A short title for the bug (max 200 characters, required)
- `description` — A detailed description of the problem (required)
- `more_details` — A `JSONField` for extra structured information such as steps to reproduce, device info, or error logs (optional, validated as a JSON object, max 10KB)
- `images` — A `JSONField` containing a list of S3 image URLs (default: empty list, max 5 images, each max 3MB). Each URL is validated using Django's `URLValidator` in the model's `clean()` method
- `status` — The current resolution status:
  - `OPEN` — Newly reported (default)
  - `IN_PROGRESS` — Being investigated
  - `RESOLVED` — Fixed or addressed
- `resolved_at` — Timestamp when the bug was resolved
- `created_at` — Timestamp when the bug was reported

### Image Upload Process

When a bug report is created with images, the files are uploaded to S3 using the `upload_files_to_s3` utility:

- Files are uploaded under the path `{shop_name}/{reporter}/bug_reports/`
- Maximum 5 files allowed per report
- Each file must be under 3MB
- Images are resized to a maximum of 1600×1600 pixels
- If the database save fails after images are uploaded, the system attempts to clean up the uploaded S3 objects

### Email Notification

When a new bug report is created, a Django signal (`bug_created_signal`) fires a Celery task (`send_bug_report_email`) that sends an email notification to the admin team. The email includes the bug title, description, shop name, reporter, additional details, and image URLs. The task retries up to 3 times with a 10-second delay between retries if the email fails to send.

### Validation

The model's `clean()` method validates the `images` field:

- It must be a list or tuple
- Each entry must be a non-empty string
- Each string must be a valid URL (checked with `URLValidator`)

### Database Indexes

- `(shop, status)` — For filtering bugs by shop and status
- `(reporter,)` — For filtering bugs by reporter

---

## Active Shop Resolution

A critical utility in the Shop module is the `get_active_shop_id` function, which is used by nearly every client-side API endpoint. This function determines which shop the current request is targeting:

1. **Header-based resolution**: The function first checks for an `X-Active-Shop-ID` header in the request. If present, it validates:
   - The shop ID is a valid integer
   - The shop exists and is not soft-deleted
   - The shop is active (`is_active=True`)
   - The requesting user has an active `ShopUser` record for that shop

2. **Fallback resolution**: If no header is provided, the function finds the user's first active `ShopUser` record (ordered by creation date) and uses that shop.

3. **Permission denied**: If no valid shop can be resolved, a `PermissionDenied` exception is raised with a descriptive message.

This function ensures that every client-side API call is properly scoped to a specific shop, and that the user has permission to access that shop.

---

## Admin-Side API Endpoints

The admin side of the system provides tools to manage and monitor shops. All admin endpoints require `IsAdminUser` permission and use `JWTAuthentication`.

### Shop CRUD (ShopViewSet)

The `ShopViewSet` provides full CRUD operations for shops:

**List Shops**
- Endpoint: `GET /admin/shops/`
- Supports search filtering by shop name, place, address, and phone number
- Returns paginated results ordered by creation date (newest first)
- The v1 version uses `select_related("subscription", "subscription__plan")` to include subscription plan details in the response
- The v1 serializer includes `total_product_count` and `subscription_plan` information

**Retrieve Shop**
- Endpoint: `GET /admin/shops/{id}/`
- Returns full shop details including all fields

**Create Shop with User**
- Endpoint: `POST /admin/shop-with-user/`
- This is the primary shop creation endpoint that creates both a user and a shop together
- The flow is:
  1. Validate and normalize the phone number
  2. Create the user record with password and secondary password
  3. Upload the shop image to S3 if provided
  4. Validate shop name and place uniqueness (case-insensitive)
  5. Parse `terms_and_conditions` from JSON string if present
  6. Create the shop record
  7. Create the `ShopUser` link with the user's role
- If the shop creation fails, the user is rolled back (deleted)
- If the entire operation fails, any uploaded S3 image is cleaned up

**Create User with Shop Assignment**
- Endpoint: `POST /admin/shop-with-user/create-with-role/`
- Creates a new user and assigns them to an existing shop with a specified role
- Validates the shop exists and is active before proceeding
- Uses a database transaction to ensure atomicity

**Update Shop**
- Endpoint: `PATCH /admin/shops/{id}/`
- Supports partial updates to any shop field
- Re-validates name/place uniqueness if either field is being changed
- Handles image upload and S3 storage
- The v1 version also blocks changes to `booking_start_id` if bookings already exist, and blocks changes to `sale_start_id` if sales already exist

**Delete Shop**
- Endpoint: `DELETE /admin/shops/{id}/`
- Performs a soft delete (sets the `deleted` timestamp)

### User–Shop Linking (ShopUserLinkAPIView)

The `ShopUserLinkAPIView` provides endpoints for managing user–shop relationships:

**Link User to Shop**
- Endpoint: `POST /admin/link-user/`
- Links an existing user to an existing shop with a specified role
- Checks that the user is not already linked to the shop
- Enforces the user count limit (base 3 + extra) for the shop

**List Users in Shop**
- Endpoint: `GET /admin/link-user/?shop_id={id}`
- Returns all active users linked to a specific shop with their names, phones, roles, and link dates

**Update User–Shop Link**
- Endpoint: `PATCH /admin/link-user/` (v3) or `PATCH /admin/link-user/{shop_user_id}/` (v1)
- Updates the role or active status of an existing user–shop link
- Validates role limits (1 Owner, 1 Manager, 2 Staff per shop) in the v1 version
- Enforces the overall user count limit when reactivating a link

**Delete User–Shop Link (v1 only)**
- Endpoint: `DELETE /admin/link-user/{shop_user_id}/`
- Hard-deletes the `ShopUser` record (not soft delete)

### User's Associated Shops (UserAssociatedShopsListView)

- Endpoint: `GET /admin/user-shops/?user_id={id}`
- Lists all shops that a specific user is linked to
- Returns shop ID, name, place, role, and link date for each association

### Shop Report Generation (v1 only)

- Endpoint: `POST /admin/shops/report/`
- Generates activity reports for a specific shop within a date range
- Supports report types: `sales`, `bookings`, `tailor_orders` (or all three if no type specified)
- Supports period grouping: `daily`, `weekly`, `monthly`, `yearly`
- Uses Django's `TruncDate`, `TruncWeek`, `TruncMonth`, `TruncYear` functions to group records
- Fills in zero-count entries for periods with no activity so the report shows a continuous timeline
- The response includes the count of records per period for charting purposes

### Idle Days Report (v1 only)

- Endpoint: `GET /admin/shops/idle-days-report/?period_days=30&threshold=15`
- Identifies shops that have been idle (no bookings, sales, or tailor orders) for a significant number of days
- Parameters:
  - `period_days` — The lookback window in days (1–365, default: 30)
  - `threshold` — Only return shops with idle days at or below this number (0–period_days, default: 15)
- The system:
  1. Finds all shops with any activity in the last 90 days
  2. For each shop, counts the number of days with bookings, sales, or tailor orders in the lookback period
  3. Calculates idle days as `period_days - active_days`
  4. Filters by the threshold and returns results sorted by idle days descending
- Each result includes shop details, active days, last activity date, total bookings/sales/tailor orders, and idle days count
- Results are paginated

### Bug Report Admin (v1 only)

The `BugReportAdminViewSet` provides admin endpoints for managing bug reports:

**List Bug Reports**
- Endpoint: `GET /admin/bugreports/`
- Supports filters: `q` (text search), `status`, `reporter` (by ID, phone, or name), `shop_id`, `start_date`, `end_date`
- Returns paginated results

**Retrieve Bug Report**
- Endpoint: `GET /admin/bugreports/{id}/`
- Returns full bug report details including shop and reporter information

**Update Bug Report Status**
- Endpoint: `PATCH /admin/bugreports/{id}/`
- Only allows updating the `status` field
- Uses `select_for_update` for thread safety
- Sets `resolved_at` timestamp when status changes to `RESOLVED`

---

## Client-Side API Endpoints

The client-side endpoints serve the mobile and web applications used by shop Owners, Managers, and Staff. These endpoints use `CutoffJWTAuthentication` and the `StandardResponseMixin` for consistent response formatting.

### User's Associated Shops (UserAssociatedShopsView)

- Endpoint: `GET /available-shops/`
- Returns shops that the current user is linked to
- Filters by active shops and active user–shop links
- Only accessible to users with roles: ADMIN, OWNER, or MANAGER

### Shop Privacy Settings (ShopPrivacySettingsView)

- Endpoint: `PATCH /shop-privacy-settings/`
- Allows the Owner to update feature access policies for their shop
- Accepts a dictionary of `{feature: password_requirement}` pairs
- Uses `update_or_create` to efficiently create or modify policies
- Requires `OwnerOnlyPermission`

### Shop Settings (ShopSettingsViewSet)

- Endpoint: `PATCH /shop-settings/update-settings/`
- Allows the Owner to update operational settings (cooling days, default action, search client, cooling period type)
- Creates the settings record with defaults if it does not exist
- Validates each field before saving
- Requires `OwnerOnlyPermission`

### Staff Management (StaffViewSet)

The `StaffViewSet` provides full CRUD for staff members within a shop:

**List Staff**
- Endpoint: `GET /staff/`
- Supports filters: `name`, `phone`, `is_deleted` (to show deleted staff)
- Returns paginated results ordered by name
- Accessible to all authenticated users

**Retrieve Staff**
- Endpoint: `GET /staff/{id}/`
- Returns details of a specific staff member

**Create Staff**
- Endpoint: `POST /staff/`
- Validates name and phone number
- Checks color uniqueness within the shop using `select_for_update`
- Requires `ManagementPermission` (Owner or Manager)

**Update Staff**
- Endpoint: `PATCH /staff/{id}/`
- Supports partial updates to name, phone, and color
- Re-validates phone uniqueness and color uniqueness
- Uses a database transaction with `select_for_update`
- Requires `ManagementPermission`

**Delete Staff**
- Endpoint: `DELETE /staff/{id}/`
- Soft-deletes by setting `is_deleted = True`
- Uses `select_for_update` for thread safety
- Requires `ManagementPermission`

**Restore Staff**
- Endpoint: `PATCH /staff/restore/{id}/`
- Restores a soft-deleted staff member
- Uses `select_for_update` for thread safety
- Requires `ManagementPermission`

### Staff Earnings (StaffEarningsViewSet)

The `StaffEarningsViewSet` provides analytics on staff member performance and earnings. It requires `SubscriptionActivePermission` and the `STAFF_ANALYTICS` feature permission for Owners and Managers.

**Earnings Report**
- Endpoint: `GET /staff-earnings/earnings-report/{staff_id}/?year=2025&month=3`
- Returns a summary of the staff member's earnings for a specific month:
  - Total bookings count, pending count, earned count
  - Total bookings pending amount and earned amount
  - Total sales count and sales earnings
  - Combined total earnings
  - Performance percentage compared to the previous month (positive for growth, negative for decline)
- Calculates booking earnings from completed bookings using annotated aggregation:
  - A booking is "earned" when `total_amount <= advance_amount`
  - A booking is "pending" when `total_amount > advance_amount`
  - Earned amount = advance amount (what the customer has paid)
  - Pending amount = total - (advance + discount)
- Sales earnings = total_amount - discount for the period

**Monthly Transactions**
- Endpoint: `POST /staff-earnings/monthly-transactions/`
- Returns a detailed, paginated list of individual bookings or sales for a specific staff member in a given month
- Request body requires `staff_id`, `month`, `year`, and `transaction_type` ("bookings" or "sales")
- Booking results include: shop_booking_id, client name, dates, amounts, earning, booked items (product names)
- Sales results include: shop_sale_id, client name, amounts, earning, sale date, products

### Activity Logs (ShopActivityLogViewSet)

- Endpoint: `GET /shop-activity-logs/`
- Lists activity logs for the current shop with filtering and pagination
- Accessible to all authenticated users
- See the Activity Log API section above for filter details

### Bug Reporting (BugReportViewSet)

- Endpoint: `POST /bug-report/`
- Creates a new bug report with title, description, optional additional details, and optional images
- Images are uploaded to S3 and URLs are stored in the bug report
- Triggers an email notification to the admin team
- Accessible to all authenticated users

### Owner Shops Financials (OwnerShopsFinancialsViewSet)

- Endpoint: `GET /shops-financials/owner-shops-financials/?period=2025-03&shop_id=5`
- Returns monthly revenue, expense, and profit data for all shops where the current user is an Owner
- Revenue is calculated based on **payment dates** (not booking/sale creation dates), matching the logic of transaction history:
  - **Booking payments**: Sum of all `PaymentDetailsV2` amounts grouped by payment creation date
  - **Cancelled booking penalties**: Advance amount minus refunded amount for cancelled bookings
  - **Sales payments**: Sum of all `SalesPaymentDetails` amounts grouped by payment creation date
  - **Tailor order payments**: Sum of all `CustomerPayment` amounts grouped by payment creation date
- Expenses include both regular `Expenses` and `ProductVariantExpense` records
- Growth percentages are calculated by comparing with the previous period (previous month for monthly view, previous year for yearly view)
- For yearly view, month-over-month growth is also calculated for each individual month
- Response includes:
  - Per-shop monthly breakdown with revenue, expense, profit, and growth
  - Per-shop totals with growth percentages
  - Overall totals across all owned shops with growth percentages
- Supports period parameter: `"2025-03"` for monthly or `"2025"` for yearly
- Optionally filter by a specific `shop_id`

---

## URL Configuration

### v3 URLs (Client-Facing + Admin)

| URL Pattern | View | Auth | Description |
|-------------|------|------|-------------|
| `admin/shops/` | ShopViewSet | Admin JWT | Shop CRUD |
| `admin/shop-with-user/` | ShopUserViewSet | Admin JWT | Create shop with user |
| `admin/link-user/` | ShopUserLinkAPIView | Admin JWT | User–shop linking |
| `admin/user-shops/` | UserAssociatedShopsListView | Admin JWT | User's shops list |
| `available-shops/` | UserAssociatedShopsView | Cutoff JWT | Client shop list |
| `shop-privacy-settings/` | ShopPrivacySettingsView | Cutoff JWT | Feature policies |
| `shop-settings/` | ShopSettingsViewSet | Cutoff JWT | Shop settings |
| `staff/` | StaffViewSet | Cutoff JWT | Staff CRUD |
| `staff-earnings/` | StaffEarningsViewSet | Cutoff JWT | Staff analytics |
| `shop-activity-logs/` | ShopActivityLogViewSet | Cutoff JWT | Activity logs |
| `bug-report/` | BugReportViewSet | Cutoff JWT | Bug reporting |
| `shops-financials/` | OwnerShopsFinancialsViewSet | Cutoff JWT | Owner financials |

### v1 URLs (Admin-Only + Legacy)

| URL Pattern | View | Auth | Description |
|-------------|------|------|-------------|
| `admin/shops/` | ShopViewSet | JWT | Shop CRUD with reports |
| `admin/shop-with-user/` | ShopUserViewSet | JWT | Create shop with user |
| `admin/link-user/` | ShopUserLinkAPIView | JWT | User–shop linking (with PATCH/DELETE by ID) |
| `admin/user-shops/` | UserAssociatedShopsListView | JWT | User's shops list |
| `admin/bugreports/` | BugReportAdminViewSet | JWT | Bug report management |
| `available-shops/` | UserAssociatedShopsView | JWT | User's shops |

The v1 API uses standard `JWTAuthentication` while v3 uses `CutoffJWTAuthentication`. The v3 endpoints include `StandardResponseMixin` for consistent response formatting with `status`, `message`, `data`, and `dev_message` fields. The v1 ShopViewSet also includes the `report` and `idle-days-report` action endpoints that are not present in v3.

---

## Admin Panel Configuration

The Django admin panel is configured with custom admin classes for all Shop module models:

**ShopAdmin**
- Displays: id, name, place, phone, address, deleted status, active status, created date
- Search by: name, phone, GST number
- Custom fieldsets organizing basic info, bookings button, and products button
- Includes a "View Bookings" button that links to the BookingV2 admin filtered by the current shop, showing the total booking count
- Includes a "View Products" button that links to the Product admin filtered by the current shop, showing the active product count

**ShopUserAdmin**
- Displays: id, user, shop, role, deleted status, active status, created date
- Search by: user name, user phone, shop name

**ShopFeaturePolicyAdmin**
- Displays: id, shop, feature, password requirement, active status, timestamps
- Filter by: feature, password requirement, active status, created date

**ShopSettingsAdmin**
- Displays: id, shop, cooling days, timestamps
- Search by: shop name

**StaffAdmin**
- Displays: id, name, phone, shop, deleted status, created date
- Filter by: shop, created date
- Search by: name, phone, shop name

**ShopActivityLogAdmin**
- Displays: id, shop, user, action, content type, object ID, created date
- Read-only (no add or change permissions, delete only for superusers)
- Date hierarchy on created_at for easy navigation

**BugReportAdmin**
- Displays: id, shop, reporter, title, status, created date, resolved date
- Filter by: status, shop, created date
- Search by: title, description, shop name, reporter phone

All soft-deletable models use `CommonSafeDeleteAdmin`, which extends `SafeDeleteAdmin` with an additional "Hard delete selected objects" bulk action for permanent removal with confirmation.

---

## Signals and Background Tasks

### Bug Report Email Signal

When a `BugReport` is created, a `post_save` signal fires the `send_bug_report_email` Celery task. The signal uses `transaction.on_commit` to ensure the email is only queued after the database transaction is successfully committed. The Celery task:

- Formats an email with the bug title, description, shop name, reporter, additional details, and image URLs
- Sends to `settings.ADMIN_EMAILS`
- Retries up to 3 times with a 10-second delay on failure
- Logs errors when the maximum retry count is exceeded

---

## Module Relationships

The Shop module connects to virtually every other module in the system:

- **Authentication** — Users are created alongside shops; phone normalization is shared
- **Subscriptions** — Shop subscription status gates access to premium features; subscription plan details are shown in admin
- **Booking** — Bookings belong to shops; cooling period settings from ShopSettings affect availability; last_booking_id on Shop provides invoice numbering
- **Sales** — Sales belong to shops; last_sale_id provides invoice numbering
- **Services** — Products and services belong to shops through ShopOwnerService; product counts are shown in admin
- **Expenses** — Expenses and ProductVariantExpenses are tracked per shop; expense data is used in the financials endpoint
- **Tailoring** — Tailor orders belong to shops; last_tailor_id provides invoice numbering; tailor payments are included in financials
- **Notifications** — Bug report creation triggers email notifications via Celery
- **S3 Storage** — Shop images and bug report images are uploaded to S3
- **Activity Logs** — All modules can create ShopActivityLog entries that are viewed through this module's API
