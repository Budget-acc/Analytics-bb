# Services Module – Rental Application

---

## Overview

The **Services Module** is the foundational layer that manages everything related to the products and inventory of a rental shop. It defines the hierarchical service structure, the product catalog, variant-level stock tracking, inter-shop stock transfers, dynamic service-specific attributes, and PDF stock report generation. Every other module in the application — bookings, expenses, sales, tailoring — depends on the products and variants created and managed within this module.

At its core, the module establishes a three-level service hierarchy: **Main Service Categories** at the top, **General Services** in the middle, and **Shop Owner Services** at the bottom. Products are always created under a Shop Owner Service, and each product can have one or more **variants** that represent specific sizes, configurations, or stock-keeping units. The module also provides a powerful **Entity-Attribute-Value (EAV)** system that allows each general service to define its own custom fields, so products under different service types can carry different metadata without requiring database schema changes.

The module is also responsible for image management through S3, product search with ranking and attribute-based filtering, product transfers between shops owned by the same user, and asynchronous PDF stock report generation with caching and chunking for large inventories.

---

## Service Hierarchy

The service hierarchy is a three-level structure that organizes how shops categorize and group their products. Understanding this hierarchy is essential because it determines how products are stored, searched, and displayed across the application.

### MainServiceCategory – The Top Level

The `MainServiceCategory` model represents the broadest classification of services. Examples include "Clothing", "Materials", "Gadgets", or "Accessories". Each main category has a unique name (enforced with a case-insensitive database constraint using `Lower("name")`), an optional description, an optional icon URL, and an `is_active` flag. The model uses `SafeDeleteModel` with `SOFT_DELETE`, meaning records are never permanently removed from the database — they are flagged with a `deleted` timestamp instead.

The case-insensitive uniqueness constraint (`unique_mainservicecategory_name_ci`) ensures that "Clothing" and "clothing" cannot both exist as separate categories. This is implemented at the database level using Django's `UniqueConstraint` with `Lower()`, which is more reliable than application-level validation alone because it protects against race conditions during concurrent creation requests.

The `is_active` field is indexed for fast filtering. Admin users can deactivate a main category without deleting it, which preserves referential integrity with the general services underneath.

### GeneralService – The Middle Level

The `GeneralService` model represents a specific type of service within a main category. For example, under the "Clothing" main category, there might be general services like "Sherwani", "Suit", "Lehenga", or "Tuxedo". Each general service has an optional foreign key to a `MainServiceCategory` (set to `SET_NULL` on deletion, meaning the general service survives even if its parent category is removed), a unique case-insensitive name, an optional description, an icon URL, and active/timestamp fields.

General services are the level at which **dynamic attribute templates** are defined. If "Sherwani" products need to track custom fields like "pollution_expiry" or "fabric_type", those attribute templates are attached to the GeneralService model. This design means all shops that offer the same general service share the same attribute definitions, ensuring consistency across the platform.

When a general service is created or updated through the admin API, its icon file is uploaded to S3 under the path `Sub_services/{service_name}/`, and the resulting URL is stored in the `icon` field. If the icon is changed during an update, the old icon is deleted from S3 to prevent orphaned files.

### ShopOwnerService – The Shop Level

The `ShopOwnerService` model creates the link between a specific shop and a general service. When a shop wants to start offering "Sherwani" rentals, an admin assigns that general service to the shop, creating a ShopOwnerService record. The model has a foreign key to `Shop` (with CASCADE deletion — if the shop is deleted, all its service assignments are removed) and a foreign key to `GeneralService` (also CASCADE).

The `unique_together` constraint on `(shop, general_service)` ensures a shop cannot be assigned the same general service twice. The model also has an `is_active` flag that can be toggled to temporarily disable a service for a shop without removing the assignment or any of the products underneath it.

The name "ShopOwnerService" is a historical artifact from when the application's User and ShopOwner entities were not yet separated. A more accurate name would be "ShopService", but it has been retained to avoid a disruptive migration.

Products are always created under a ShopOwnerService, establishing the full chain: MainServiceCategory → GeneralService → ShopOwnerService → Product. This means every product in the system can be traced back through its service assignment to the shop that owns it and the category it belongs to.

---

## Product Model

The `Product` model is the central entity in the services module. It stores every item that a shop offers for rental or sale. Each product belongs to exactly one `ShopOwnerService` through the `shop_service` foreign key (CASCADE deletion), and has a `related_name` of "products" so that all products under a service can be queried with `shop_service.products.all()`.

### Core Fields

The `name` field is a CharField with a maximum length of 200 characters. It stores the display name of the product (e.g., "Royal Sherwani"). During creation and update, the view applies a capitalization rule: the first letter is uppercased, and the rest of the name is preserved exactly as the user typed it.

The `description` field is an optional TextField for additional product details. The `color`, `category`, and `model` fields are all optional CharFields with a 50-character limit. These fields serve as the primary search dimensions — the product list API supports searching by any of these fields individually.

The `image` and `thumbnail_image` fields are URLFields (max 500 characters) that store the S3 URLs for the product's main image and a 100×100 pixel thumbnail respectively. When a product is created or updated with an image, the view validates the file (size limit of 3MB, allowed MIME types, maximum dimensions of 10000×10000), converts it to RGBA PNG format, generates a thumbnail using Pillow's LANCZOS resampling, and uploads both to S3 under the path `{shop_name}/product_images/{service_name}/{product_name}_{uuid}.png`.

### Pricing Fields

The product has four distinct price fields, each serving a different purpose:

- `purchase_price` (PositiveIntegerField, nullable) — The cost price or acquisition price of the product. This is used internally for profit calculations and expense tracking but is not exposed to customers.

- `price` (PositiveIntegerField, default 0) — The daily rental price. This is the primary price displayed to customers and used for booking calculations. It defaults to zero so that products can be created before pricing is finalized.

- `sale_price` (DecimalField, 10 digits, 2 decimal places, nullable) — A discounted selling price, used when the product is being sold outright rather than rented.

- `hourly_rental_price` (DecimalField, 10 digits, 2 decimal places, nullable) — For products that support hourly rental instead of daily rental. This field is optional and only relevant for specific service types.

### Uniqueness Constraint

The product model has a sophisticated uniqueness constraint (`unique_product_ci_per_shop`) that prevents duplicate products within the same shop service. The constraint operates on the combination of `Lower(Coalesce(name, ""))`, `Lower(Coalesce(color, ""))`, `Lower(Coalesce(model, ""))`, `Lower(Coalesce(category, ""))`, and `shop_service`, with a condition that `deleted__isnull=True` — meaning it only applies to non-soft-deleted records.

The `Coalesce` with an empty string handles nullable fields so that two products with NULL color are treated as having the same (empty) color rather than being treated as distinct. The `Lower()` ensures case-insensitive comparison, so "Red" and "red" are considered the same. The `deleted__isnull=True` condition means soft-deleted products do not block creation of new products with the same name.

In addition to the database constraint, the create and update views perform an explicit duplicate check using annotated `Lower()` queries before saving. This provides a user-friendly error message ("A product with the same name, color, model, and category already exists") instead of an opaque database integrity error.

### Product Limit Enforcement

During product creation, the system enforces a per-shop product limit. The view locks the shop row using `select_for_update()` to prevent race conditions, counts all non-soft-deleted products across all services for that shop, and compares against the plan limit (a base of 150 plus any `extra_stock_limit` configured on the shop). If the limit is reached, creation is blocked with a message asking the user to upgrade their plan.

### Indexes

The Product model has an extensive set of database indexes optimized for the most common query patterns:

- Individual field indexes on `shop_service`, `name`, `color`, `category`, `model`, `is_active`, `updated_at`, and `created_at`
- Composite indexes on `(shop_service, is_active, -id)` for the primary product list query (filtered by service, ordered by newest first)
- Composite index on `(shop_service, updated_at)` for PDF cache versioning
- Composite index on `(is_active, shop_service_id)` for cross-service queries

---

## ProductVariant Model

The `ProductVariant` model represents individual stock-keeping units within a product. A product like "Royal Sherwani" might have variants for sizes 38, 40, 42, and 44. Each variant tracks its own stock level and can have its own price override.

### Core Fields

The `product` foreign key links every variant to its parent product (CASCADE deletion, `related_name="variants"`). The `attribute` field is a CharField (max 50) that stores the variant's distinguishing characteristic — typically a size (30, 32, M, L, XL) but it can be any text. During creation, the view capitalizes the first letter of the attribute value.

The `price` field is an optional PositiveIntegerField that overrides the product's base price for this specific variant. The `stock` field is a PositiveIntegerField (default 0) that tracks how many units of this variant are currently available. The `fabric_length` field is a DecimalField (10 digits, 2 decimal places) used specifically for material products where inventory is tracked by length in meters rather than by count.

### QR Code Support

Each variant supports two code-based identification fields:

- `unique_code` (CharField, max 64, unique, indexed) — An auto-generated internal code in the format `S{service_id}-P{product_id}-V{variant_id}`. This is generated after variant creation and is used for internal scanning and tracking. The serializer includes a retry mechanism with row-level locking (`select_for_update`) to handle concurrent creation safely.

- `external_qr_code` (CharField, max 128, indexed, nullable) — A customer's existing QR code or barcode value. This allows shops to scan products using their pre-existing labeling system. Uniqueness is enforced at the shop level during creation and update — the view uses `select_for_update()` to lock existing rows and prevent race conditions during validation.

### Smart Save and Timestamp Tracking

The variant model has two separate timestamp fields that serve different purposes:

- `structure_updated_at` — Updated when structural fields change (name, attribute, price, external_qr_code, etc.)
- `quantity_updated_at` — Updated when quantity fields change (stock, fabric_length)

The model overrides the `save()` method with custom logic to maintain this separation. When `update_fields` is provided and contains only quantity-related fields (`stock`, `fabric_length`, `quantity_updated_at`), the method skips updating `structure_updated_at`. This distinction is critical for the PDF report caching system: if only stock quantities change but the product structure hasn't changed, the system can use a different cache invalidation strategy than if the product catalog structure has been modified.

The smart save logic works as follows:

1. If `update_fields` is provided and all fields are quantity-only fields, `structure_updated_at` is not modified.
2. If `update_fields` contains any non-quantity field, `structure_updated_at` is set to the current time and added to `update_fields` if not already present.
3. If no `update_fields` is specified (full save), `structure_updated_at` is always updated.
4. For quantity updates, `quantity_updated_at` is automatically added to `update_fields` to ensure it is persisted.

### Stock Validation During Updates

When updating a variant's stock through the `ProductVariantViewSet`, the system validates that the new stock level is not below the maximum booked quantity on any future date. It queries the `ProductVariantAvailability` table (from the booking module) to find the highest `booked_quantity` across all future dates, and rejects the update if the new stock would be less than that value. This prevents situations where reducing stock would cause existing bookings to exceed available inventory.

---

## Dynamic Attributes System (EAV Pattern)

The services module implements an **Entity-Attribute-Value (EAV)** pattern that allows each general service to define custom fields for its products. This means "Sherwani" products can have different metadata than "Gadget" products without requiring database schema changes.

### ServiceAttributeTemplate

The `ServiceAttributeTemplate` model defines what custom attributes are available for a given general service. Each template has:

- `general_service` — FK to GeneralService (CASCADE, `related_name="attribute_templates"`)
- `attribute_key` — A SlugField (max 100) used as the identifier in API requests (e.g., "pollution_expiry", "fabric_type")
- `attribute_label` — A human-readable label (e.g., "Pollution Certificate Expiry", "Fabric Type")
- `attribute_type` — One of: text, number, decimal, boolean, choice, date
- `choices` — A JSONField storing valid options for choice-type attributes (e.g., `["Cotton", "Silk", "Polyester"]`)
- `applies_to` — Either "product" or "variant", determining whether the attribute attaches to products or individual variants
- `is_required` — Boolean flag for mandatory attributes
- `display_order` — Integer for ordering attributes in the UI

The model is ordered by `display_order` and has composite indexes on `(general_service, applies_to)` and `(general_service, display_order)` for efficient querying.

### ServiceAttributeValue

The `ServiceAttributeValue` model stores the actual values for each product or variant. It has nullable foreign keys to both `Product` and `ProductVariant` (one of which will be set depending on the template's `applies_to` setting), a foreign key to the `ServiceAttributeTemplate`, and a `value` field stored as JSON. The JSON storage allows flexibility — text values are stored as strings, numbers as integers, dates as ISO-format strings, and choices as the selected option string.

### Attribute Validation

When a product is created or updated with attributes, the `_validate_and_save_product_attributes` method in `ProductViewSet` performs comprehensive validation:

1. **Unknown attribute check** — Any attribute key not defined in the service's templates is rejected with an error listing valid attributes.
2. **Required field check** — Templates marked as `is_required` must have non-empty values.
3. **Type-specific validation** — Each attribute type has its own validation logic:
   - `text` — Must be a string; whitespace is trimmed
   - `number` — Must be convertible to an integer
   - `decimal` — Must be convertible to a Decimal
   - `boolean` — Accepts true/false, 1/0, yes/no (case-insensitive)
   - `choice` — Must be one of the values defined in the template's `choices` list
   - `date` — Supports multiple formats (YYYY-MM-DD, DD-MM-YYYY, MM/DD/YYYY, DD/MM/YYYY) and normalizes to YYYY-MM-DD

Attributes can be sent in two formats: as a JSON string under the `attributes` key, or as bracket-notation keys like `attributes[pollution_expiry]`. The JSON payload is limited to 10KB as a denial-of-service protection measure.

### Attribute Search

The product list API supports searching by custom attribute values. When the `search_by` parameter doesn't match any standard product field (name, color, category, model), the system treats it as a service attribute key. It first validates that the attribute template exists for the current service and belongs to the correct service (preventing cross-service data leaks), then constructs a subquery using `Exists` and `Cast` to find products with matching attribute values. If the attribute key is invalid or doesn't exist, the query returns an empty result set rather than an error, following a fail-safe approach.

---

## Product Transfer System

The product transfer system allows shop owners to move stock between their shops. This is managed through the `ProductTransferHistory` model and the `ProductTransferViewSet`.

### ProductTransferHistory Model

Each transfer record captures:

- `transfer_id` — A UUID generated automatically for unique identification
- `status` — One of COMPLETED, FAILED, or PENDING
- `source_shop`, `source_product`, `source_variant` — The origin of the transferred stock (all CASCADE foreign keys)
- `destination_shop` — The target shop (CASCADE)
- `destination_product`, `destination_variant` — The destination product and variant (nullable, as they may be created during the transfer)
- `quantity_transferred` — Number of units moved
- `transfer_type` — Either "TO_EXISTING" (merged into an existing product at the destination) or "CREATE_NEW" (a new product is cloned at the destination)
- `initiated_by` — FK to User (CASCADE)
- `transfer_date` — Auto-set on creation
- `product_snapshot` — A JSONField that captures the source product's name, model, category, color, price, variant attribute, and original stock at the time of transfer, preserving the historical record

The model is ordered by `-transfer_date` and has composite indexes on `(source_shop, transfer_date)`, `(destination_shop, transfer_date)`, and `(initiated_by, transfer_date)` for efficient history queries.

### Transfer Workflow

The transfer process involves two steps:

**Step 1: Match Product** (`POST /transfer-product/match-product/`)

The user provides `from_variant_id` (the source variant to transfer from) and `to_shop_id` (the destination shop). The system:

1. Retrieves the source product and its general service
2. Verifies the user has OWNER or MANAGER role in both the source and destination shops
3. Finds or validates the destination shop has a matching ShopOwnerService for the same general service
4. Searches for products in the destination service that match the source product, using a scoring system:
   - Score 4: Exact match on name, model, category, and color (case-insensitive)
   - Score 3: Exact match on name only
   - Score 2: Name contains the source product's name
   - Score 1: Name contains the first word of the source product's name
5. Returns a paginated list of matching products, sorted by match score

**Step 2: Transfer Stock** (`POST /transfer-product/transfer-stock/`)

The user provides `from_variant_id`, `to_shop_id`, `transfer_quantity`, and optionally `to_product_id` (if transferring to an existing product). The entire operation runs inside an atomic transaction:

- **To Existing Product** — If `to_product_id` is specified, the system finds the destination product, locates or creates a variant with the same attribute name, adds the transfer quantity to the destination variant's stock, deducts from the source variant's stock, and creates a transfer history record with type "TO_EXISTING". If the destination variant was previously soft-deleted, it is undeleted and its stock is reset to zero before adding the transfer quantity.

- **Create New Product** — If no `to_product_id` is specified, the system first checks that no similar product already exists in the destination service (using case-insensitive matching on name, model, category, and color). If clear, it clones the source product (including image URLs) into the destination service, creates a variant with the transferred stock, deducts from the source, and records the transfer with type "CREATE_NEW".

### Permission Model

Product transfers require the `SubscriptionActivePermission` (the shop's subscription must be active) and a role-based permission that restricts transfers to users with OWNER or MANAGER roles. The `_check_user_permission` method verifies the user's role in all involved shops by querying the `ShopUser` model.

---

## PDF Stock Report Generation

The services module includes a comprehensive PDF report generation system that creates stock reports for shop products. The system is designed to handle shops with thousands of products through asynchronous generation, chunking, caching, and S3 storage.

### Product Count Restriction

Before any generation work begins, `generate_pdf_report` checks the total number of active products on the selected service (unfiltered — independent of `search`, `in_stock_only`, or price filters). If the count exceeds 1,000 (`MAX_PRODUCTS_FOR_STOCK_REPORT`, configurable via Django settings), the request is rejected immediately with a validation error ("Cannot generate Stock Report PDF: too many products."). No `PDFGenerationTask` is created, no cache lookup happens, and no Celery task is queued.

This is a distinct, earlier gate than the 5000-product chunking threshold described below (`MAX_PRODUCTS_PER_PDF`). Since any service over 1,000 products is now rejected before the chunking decision is made, the multi-PDF chunking path is effectively unreachable under current limits. The chunking logic remains in place in case the two limits are reconsidered independently in the future.

### PDFGenerationTask Model

The `PDFGenerationTask` model tracks every PDF generation request. It stores:

- `task_id` — A unique UUID string identifying the Celery task
- `shop` — FK to Shop (CASCADE, `related_name='pdf_tasks'`)
- `s3_key` — The S3 object key where the generated PDF is stored
- `download_url` — A pre-signed S3 URL for downloading the PDF
- `snapshot_version`, `quantity_version`, `layout_version` — Version identifiers used for cache invalidation
- `status` — One of: pending, processing, chunking, completed, failed, expired
- `estimated_count`, `estimated_time`, `actual_generation_time` — Metrics for user feedback and performance monitoring
- `total_chunks` — For multi-PDF generation when products exceed the chunk limit
- `request_hash` — An MD5 hash of the request parameters used for duplicate detection
- `was_cached`, `cache_hit_type` — Flags indicating if the result was served from cache
- `request_params` — A JSONField storing all request parameters for reproducibility
- `url_expires_at` — When the pre-signed download URL expires (24 hours)

The model includes `is_url_expired()` and `mark_expired()` helper methods for URL lifecycle management.

### Cache Strategy (PDFSnapshotManager)

The `PDFSnapshotManager` class in `services/utils/pdf_snapshot.py` implements a selective caching strategy:

- **Base case (no filters)** — When a PDF is requested without any search, price, or stock filters, the system computes a cache key based on the latest `updated_at` timestamp from products, the latest `structure_updated_at` from variants, the latest `quantity_updated_at` from variants, and the layout version. If a PDF with this cache key already exists in S3, it is served directly with a fresh pre-signed URL. This means that as long as no product or variant data has changed, the same PDF is reused.

- **Filtered requests** — When filters are applied (search term, price range, in-stock-only), caching is disabled and a new PDF is generated every time. Old filtered PDFs are cleaned up after 24 hours via an asynchronous cleanup task.

The S3 key structure reflects this distinction: base case PDFs are stored as `pdf_reports/{shop_id}/cached_{cache_key}.pdf`, while filtered PDFs use `pdf_reports/{shop_id}/filtered_{request_hash}.pdf`.

### Single PDF Generation (≤5000 Products)

For shops with up to 5000 products, the `generate_pdf_parallel_task` Celery task handles generation with a 5-minute time limit:

1. A distributed lock is acquired using Django's cache backend to prevent concurrent generation of the same PDF
2. If the lock can't be acquired immediately, the task retries with exponential backoff (2s, 3s, 5s, 8s delays), checking the cache between retries in case another task completed the generation
3. Products are loaded using an iterator with a chunk size of 500 to manage memory
4. The `ProductReportPDFService` generates the PDF using ReportLab with product images, variant details, and stock information
5. The PDF is uploaded to S3 and a pre-signed download URL is generated
6. The task record is updated with completion details and timing metrics
7. The lock is released in a `finally` block

### Multi-PDF Generation (>5000 Products)

When product count exceeds 5000, the `generate_multi_pdf_task` coordinator task splits the work:

1. The coordinator creates child `PDFGenerationTask` records for each 5000-product chunk
2. Each chunk is processed sequentially by `generate_pdf_chunk_sequential` tasks to avoid memory exhaustion
3. The coordinator tracks progress and updates chunk statuses in the parent task's `request_params`
4. The coordinator has a 15-minute time limit; individual chunks have 5-minute limits
5. Upon completion, all chunk download URLs are returned in the response

### PDF Content Generation

The `ProductReportPDFService` in `services/utils/product_report_pdf.py` generates professional PDF reports using ReportLab. Features include:

- Shop header with name, address, phone number, and logo
- Tabular product listing with variant details, stock levels, and pricing
- Thumbnail images embedded in the PDF (fetched via thread pool for parallel downloading)
- Custom fonts and styling with color theming
- Global caches for styles, SVG backgrounds, placeholder images, and resized image bytes to minimize memory usage and repeated computation

### PDF Merging

The `PDFChunkMerger` in `services/utils/pdf_merger.py` handles merging multiple PDF chunks using PyPDF2. It uses streaming and periodic garbage collection to manage memory for large merges, with configurable chunk sizes based on total product count.

### Duplicate Request Detection

Before starting a new PDF generation task, the `generate_pdf_report` action checks for existing tasks with the same `request_hash` that are still pending or processing (within the last 15 minutes). If found, the existing task ID is returned instead of creating a duplicate, preventing wasteful parallel generation of identical PDFs.

---

## S3 Service

The `S3Service` class in `services/s3_service.py` is a utility singleton that manages all S3 interactions for the services module. It uses a thread-safe client initialization with `threading.Lock` and configures the boto3 client with connection pooling (100 max connections) for high throughput.

Key methods:

- `upload_pdf(s3_key, pdf_bytes, expires_in_hours)` — Uploads PDF bytes to S3 with multipart upload for files larger than 5MB, returns a pre-signed download URL
- `download_file(s3_key)` — Downloads a file from S3 as bytes
- `delete_file(s3_key)` — Deletes a file from S3
- `file_exists(s3_key)` — Checks if a file exists in S3
- `get_presigned_url(s3_key, expires_in_hours)` — Generates a pre-signed URL for downloading

### Image Upload Utilities

The `services/v3/utils.py` file provides image-specific upload functions:

- `upload_image_to_s3(image_file, path_prefix, max_size)` — Resizes an image to the specified maximum dimensions and uploads to S3
- `upload_product_image_with_thumbnail_to_s3(image_file, path_prefix)` — Uploads the original image and generates a 100×100 thumbnail, returning both URLs
- `delete_s3_file(file_url)` — Extracts the S3 key from a full URL and deletes the file

---

## API Endpoints

The services module exposes its APIs through two versions: `v1` and `v3`. Both versions have identical URL patterns, but v3 uses `CutoffJWTAuthentication`, `StandardResponseMixin` for consistent response formatting, and `SubscriptionActivePermission` for subscription enforcement. The v1 version uses standard `JWTAuthentication` without subscription checks.

### Admin APIs

These endpoints require `IsAdminUser` permission and are used by the platform administration team.

#### GeneralServiceViewSet (`admin/general-services/`)

Full CRUD for general services with icon upload support:

- **GET /admin/general-services/** — Lists all general services with pagination, ordered by `-id`
- **GET /admin/general-services/{id}/** — Retrieves a single general service
- **POST /admin/general-services/** — Creates a general service. Accepts multipart form data with an optional `icon` file. The icon is uploaded to S3 under `Sub_services/{name}/`. Handles duplicate name errors with a user-friendly message.
- **PATCH /admin/general-services/{id}/** — Partially updates a general service. If a new icon is provided, the old one is deleted from S3.
- **DELETE /admin/general-services/{id}/** — Soft-deletes a general service.

Additional admin actions:

- **POST /admin/general-services/assign-to-shop/** — Assigns one or more general services to a shop by creating ShopOwnerService records. Accepts `shop_id` and `service_ids` (list). Skips already-assigned services.
- **GET /admin/general-services/shop-services/?shop_id={id}** — Lists all services assigned to a shop with their general service details (name, description, icon, main category).
- **POST /admin/general-services/shop/change-service-status/** — Toggles the `is_active` status of a ShopOwnerService by its ID.

#### MainServiceCategoryViewSet (`admin/main-services/`)

Full CRUD for main service categories:

- **GET /admin/main-services/** — Lists all categories with pagination
- **GET /admin/main-services/{id}/** — Retrieves a single category
- **POST /admin/main-services/** — Creates a category with optional icon upload to `Main_services/{name}/` (resized to 100×100)
- **PATCH /admin/main-services/{id}/** — Updates a category; handles icon replacement with old icon deletion
- **DELETE /admin/main-services/{id}/** — Soft-deletes a category

#### ProductAdminViewSet (`admin/product/`)

Admin-only product management endpoints:

- **GET /admin/product/transfer-history/** — Lists all product transfer records with full related data. Supports `shop_id` and `type` (outgoing/incoming/all) query parameters.
- **GET /admin/product/transfer-stats/** — Returns aggregate transfer statistics: total transfers, this month's transfers, outgoing/incoming counts, and total quantity transferred.
- **GET /admin/product/products-with-variants/?shop_owner_service_id={id}** — Lists all products with their variant details for a specific shop service. Supports `search` and `search_key` (name/color/model/category) parameters. Returns a custom response that includes shop and service metadata alongside paginated products.

### Client APIs

These endpoints are used by the mobile application and require `IsAuthenticated` permission with shop-level authorization.

#### AllServicesView (`all/`)

- **GET /all/** — Returns all active general services. No authentication required for this public endpoint.

#### SelectedServicesView (`selected/`)

- **GET /selected/** — Returns the general services assigned to the user's currently active shop, including main category names and icons. Uses `StandardResponseMixin` for consistent response formatting.

#### ProductViewSet (`products/`)

This is the most complex viewset in the module, handling the complete product lifecycle. It uses `ProductSearchThrottle` (60 requests per minute) to protect against search abuse.

**CRUD Operations:**

- **POST /products/** — Creates a product with variants, images, and service attributes in a single atomic transaction. The flow is:
  1. Extract and validate attributes (JSON or bracket notation)
  2. Parse variant data from indexed form fields (`variants[0][attribute]`, `variants[0][stock]`, etc.)
  3. Validate the shop service exists and belongs to the user's shop
  4. Parse and validate all price fields (price, purchase_price, sale_price, hourly_rental_price)
  5. Check for duplicate products (case-insensitive)
  6. Validate via ProductSerializer
  7. Inside atomic transaction: enforce product limit, validate external QR codes, validate service attributes, process image upload, create product, bulk create variants, generate unique codes, save attribute values
  8. Return success response

- **GET /products/?shop_service_id={id}** — Lists products for a shop service with comprehensive search and filtering:
  - `search` + `search_by` — Search by name, color, category, model (standard fields with ranking: exact match → starts with → contains), or by any service attribute key (using the EAV subquery system)
  - `min_price` / `max_price` — Price range filtering
  - `in_stock_only` — Filter to products with stock > 0
  - For gadget services, standard field searches also search variant attributes
  - Results are paginated using `StandardPageNumberPagination`

- **GET /products/{id}/** — Retrieves a single product with shop ownership verification, prefetched variants, and attribute values

- **PATCH /products/{id}/** — Updates a product with comprehensive security:
  - IDOR protection via shop_id check in the query
  - Soft-deleted products cannot be updated
  - Image validation (size, MIME type, dimensions, decompression bomb protection)
  - Secure file paths with sanitized names (regex to prevent path traversal)
  - Atomic transaction with row-level locking
  - Service attribute validation and partial update (only specified attributes are modified)
  - Old image cleanup after successful update (non-blocking)
  - Memory leak prevention with proper BytesIO cleanup in `finally` block

- **DELETE /products/{id}/** — Soft-deletes a product after shop ownership verification. Creates an activity log entry.

**Additional Actions:**

- **GET /products/variant-bookings/{variant_id}/** — Lists all bookings for a specific variant with optional status filtering (upcoming/completed)
- **GET /products/product-bookings/{product_id}/** — Lists all bookings across all variants of a product
- **GET /products/monthly-summary/{product_id}/** — Returns month-by-month expense and earning totals for a product. Earnings are calculated by distributing booking discounts proportionally across booked items.
- **GET /products/material-usage-orders/{variant_id}/** — Lists tailor orders that used a specific material variant, with status filtering
- **GET /products/list-with-variants/** — Lists products with variants across one or multiple shop services. Supports all search/filter parameters plus `generate_pdf` flag for inline PDF download and `paginate` flag to disable pagination.
- **GET /products/generate-pdf-report/** — Initiates async PDF generation via Celery. Returns a task ID for status polling. Handles duplicate detection, cache checking, and automatic chunking for large datasets.
- **GET /products/pdf-status/{task_id}/** — Polls the status of a PDF generation task. Returns progress information for pending tasks, download URLs for completed tasks (single or multi-PDF), and error details for failed tasks.
- **GET /products/lookup-externalqr/{code}/** — Looks up a product by external QR code or internal unique code. Validates shop ownership before returning product data.

#### ProductVariantViewSet (`products/{product_id}/variants/`)

Manages variants within a specific product:

- **POST /products/{product_id}/variants/** — Creates a variant with external QR code validation (shop-level uniqueness with row-level locking). Auto-generates a unique code.
- **PATCH /products/{product_id}/variants/{id}/** — Updates a variant. Stock reduction is validated against future booking availability. External QR code uniqueness is re-validated excluding the current variant.
- **DELETE /products/{product_id}/variants/{id}/** — Soft-deletes a variant after shop ownership verification. Creates an activity log.

#### ShopProductSearchAPIView (`product-search/`)

A dedicated cross-service product search endpoint:

- **GET /product-search/** — Searches products across all active services for a shop (or a specific service if `shop_service_id` is provided). By default, it excludes products from "Materials" category services unless a specific service is requested. Supports the same search, price, and stock filters as the main product list, plus gadget-specific variant attribute searching.

#### ProductTransferViewSet (`transfer-product/`)

Manages inter-shop stock transfers (requires OWNER or MANAGER role and active subscription):

- **POST /transfer-product/match-product/** — Finds matching products in the destination shop using a scoring algorithm
- **POST /transfer-product/transfer-stock/** — Executes the stock transfer in an atomic transaction
- **GET /transfer-product/transfer-history/** — Lists transfer history for a shop with type filtering
- **GET /transfer-product/transfer-stats/** — Returns aggregate transfer statistics

---

## Serializers

The module uses several serializers for different response formats:

- `GeneralServiceSerializer` — Full model serialization for admin CRUD
- `GeneralServiceListSerializer` — Compact serialization (id, name, description, icon, is_active) for public listing
- `MainServiceCategorySerializer` — Full model serialization with `is_active` defaulting to True
- `ShopOwnerServiceSerializer` — Flattens the service hierarchy: includes `service_name`, `description`, `icon`, and `main_service_name` from the related GeneralService and MainServiceCategory
- `ProductVariantSerializer` — Serializes variant data with auto-generated `unique_code` (includes a retry mechanism with row-level locking for safe concurrent code generation)
- `ProductSerializer` — Full product serialization with nested variants (filtered by `in_stock_only` if requested) and service attributes (returned as a key-value dictionary). Validates that at least one variant is provided, checks for duplicate variant attributes, and prevents modification of `shop_service_id` on updates. Applies capitalization rules: product name gets first letter uppercased, while color/category/model are fully capitalized-then-lowered.
- `ProductWithMainServiceSerializer` — Extends product serialization to include the main service category name
- `ProductTransferHistorySerializer` — Serializes transfer records with all related entity details

---

## Admin Configuration

The Django admin is configured with detailed views for all models:

- `CommonSafeDeleteAdmin` — A base class extending `SafeDeleteAdmin` that adds a `hard_delete_selected` bulk action with a confirmation template, allowing admins to permanently remove soft-deleted records when needed.

- `MainServiceCategoryAdmin` — Search by name, filter by `is_active`
- `GeneralServiceAdmin` — Search by name, filter by `is_active` and `deleted`
- `ShopOwnerServiceAdmin` — Search by shop name and service name, filter by `is_active` and `deleted`
- `ProductAdmin` — The most detailed admin view. Includes:
  - Full fieldsets organized into sections
  - A `variant_details` read-only field that renders an HTML table of all active variants with their attributes, stock, and prices
  - A `view_bookings_button` that links to the filtered BookingV2 admin changelist showing all bookings for that product, with a count badge
  - Custom `lookup_allowed` override to support filtering by parent shop ID from the Shop admin
  - Search by name, color, model, category; filter by is_active, category, deleted
- `ProductVariantAdmin` — Displays shop name via `get_shop` method, includes a `variant_bookings` button linking to BookingV2 admin filtered by variant
- `ProductTransferHistoryAdmin` — Displays all transfer fields, filterable by status, type, source/destination shop, and date
- `PDFGenerationTaskAdmin` — Displays task status, timestamps, S3 key, and download URL
- `ServiceAttributeTemplateAdmin` — Compact display of attribute key, service, type, and applies_to
- `ServiceAttributeValueAdmin` — Shows product, variant, attribute, and value

---

## Custom Exceptions and Rate Limiting

The module defines three custom exceptions for structured error handling in attribute search:

- `InvalidAttributeKeyError` — Raised when an attribute key doesn't exist for the service
- `AttributeSearchError` — Raised when attribute search encounters a processing error
- `ServiceMismatchError` — Raised when an attribute belongs to a different service than expected (a security concern for cross-service data leaks)

The `ProductSearchThrottle` class extends DRF's `UserRateThrottle` with a rate of 60 requests per minute. This protects against search spam, expensive database queries from complex attribute searches, CPU-intensive Cast and Exists operations, and potential denial-of-service attacks through repeated complex queries.

---

## URL Configuration

Both `v1` and `v3` share the same URL patterns:

| URL Pattern | View | Type |
|---|---|---|
| `admin/general-services/` | GeneralServiceViewSet | Admin |
| `admin/main-services/` | MainServiceCategoryViewSet | Admin |
| `admin/product/` | ProductAdminViewSet | Admin |
| `products/` | ProductViewSet | Client |
| `products/<product_id>/variants/` | ProductVariantViewSet | Client |
| `transfer-product/` | ProductTransferViewSet | Client |
| `all/` | AllServicesView | Public |
| `selected/` | SelectedServicesView | Client |
| `product-search/` | ShopProductSearchAPIView | Client |

The key difference between versions is the authentication and middleware stack:
- **v1** uses `JWTAuthentication` with basic `IsAuthenticated` permission
- **v3** uses `CutoffJWTAuthentication`, `StandardResponseMixin` for structured responses, and `SubscriptionActivePermission` for subscription enforcement

---

## Module Relationships

The Services module integrates with nearly every other module in the application:

- **Booking Module** — Products and variants are the items that get booked. The booking system references `Product` and `ProductVariant` through `BookedProductVariant`. The `ProductVariantAvailability` table tracks daily booking counts per variant, which the services module queries during stock updates and transfers.

- **Expenses Module** — `ProductVariantExpense` records track expenses against specific variants. The monthly summary action aggregates these expenses to show profitability per product.

- **Sales Module** — Products can be sold outright (not just rented) using the `sale_price` field. Sale records reference products and variants.

- **Shop Module** — Every product chain starts with a `Shop`. The services module uses `get_active_shop_id()` from the shop utilities to determine the current user's active shop for all operations. Product limits are enforced based on the shop's plan configuration (`extra_stock_limit`).

- **Subscriptions Module** — The v3 APIs enforce `SubscriptionActivePermission` and feature-specific permissions through `FeaturePermissionFactory` and `FeatureRolePermissionFactory`. Product transfers specifically require the "TRANSFER" feature permission with OWNER or MANAGER roles.

- **Tailoring Module** — Material products (tracked by `fabric_length`) can be consumed by tailor orders. The `material-usage-orders` action shows which tailor orders have used a specific material variant.

- **Notifications Module** — Can be triggered by product-related events.

- **PDF Service / S3** — The module directly manages S3 uploads for product images and PDF reports. The `S3Service` singleton, `PDFSnapshotManager`, `ProductReportPDFService`, and `PDFChunkMerger` form the complete PDF generation pipeline.

- **Activity Logging** — Product and variant deletions create activity log entries through `create_activity_log` for audit trail purposes.
