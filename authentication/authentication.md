# Authentication Module – Rental Application

---

## Overview

The **Authentication Module** is responsible for managing user identity, login, and access control within the entire system. It provides the core mechanisms for user authentication, user management, access control enforcement, and role-based restrictions for backend APIs.

All users interacting with the system — whether through the mobile application, the desktop web interface, or the admin panel — must be authenticated through this module before accessing any other backend modules such as bookings, sales, customization works, ledger, and stock management.

The system uses the user's **phone number** as the primary login identifier. Phone numbers are stored in E.164 format with the country code (default region: India), which also enables integration with WhatsApp messaging and notification features across the application.

The module is structured into two distinct sections:

- **Admin-Side APIs** — Administrative operations for managing users: creating, updating, listing, deleting, and resetting passwords
- **User-Side APIs** — User-facing operations: login, profile retrieval, password management, and wallet authentication

---

## User Model

The `User` model is the central model in the authentication module. It represents every individual who can access the system, including shop Owners, Managers, Staff members, and system administrators. The model extends Django's authentication framework — specifically `AbstractBaseUser` and `PermissionsMixin` — to support custom login behavior based on phone numbers rather than usernames.

The model also extends `SafeDeleteModel` with the `SOFT_DELETE` policy, meaning when a user is deleted, they are not physically removed from the database. Instead, a `deleted` timestamp is set. This preserves all historical relationships — bookings, payments, activity logs — that reference the user.

### Phone Number

The `phone` field is a `PhoneNumberField` with `region="IN"` and a unique constraint. This is the **primary login identifier** for the system. The phone number is stored with the country code in E.164 format (for example, `+919876543210`). During login, the system normalizes phone input from various formats — `+91XXXXXXXXXX`, `91XXXXXXXXXX`, `0XXXXXXXXXX`, or plain `XXXXXXXXXX` — into the standard E.164 format before performing authentication.

### Email

The `email` field is an optional `EmailField`. It is stored for informational purposes but is **not used for authentication**. Users log in exclusively through their phone number. The email is validated for correct format when provided but does not participate in the login or password recovery flow.

### Name Fields

The model stores the user's name in two fields:

- `full_name` — A `CharField` (max 200 characters) that represents the user's first name or display name
- `last_name` — A `CharField` (max 100 characters) that is combined with `full_name` to represent the complete name

These fields are used throughout the system for display purposes — in booking records, activity logs, staff assignments, and admin listings.

### Password

The `password` field is the user's primary authentication password. It is stored using Django's built-in password hashing system (`set_password` / `check_password`), which means the actual password is never stored in plain text. When a user logs in, the system hashes the provided password and compares it against the stored hash.

### Secondary Password (Legacy)

The `secondary_password` field was previously used for accessing sensitive shop-related data, such as ledger information. When a user wanted to view the shop's financial data, the system would prompt for this secondary password as an additional security layer.

However, this approach is no longer used in the current system. Access control for sensitive operations has been moved to **shop-level password protection** instead — the shop itself has a `password` field that controls access to protected features. The secondary password field remains in the model for backward compatibility with older versions of the application, but new features use the shop password exclusively.

### Password Changed At

The `password_changed_at` field is a `DateTimeField` that stores the exact timestamp of when the user last changed their password. This field plays a critical role in session management and security.

When a user changes their password, the `password_changed_at` value is updated. The `CutoffJWTAuthentication` middleware then checks every incoming request: if the JWT token was issued before the `password_changed_at` timestamp, the token is rejected. This effectively forces a logout on all devices when the password changes, ensuring that old sessions cannot continue using stale credentials.

This mechanism works in conjunction with the `CutoffTokenRefreshView`, which also checks the token's `iat` (issued-at) claim against `password_changed_at` before issuing a new access token. If the token was issued before the password change, the refresh is denied and the user must log in again.

### Parent

The `parent` field is a self-referencing foreign key (`on_delete=SET_NULL`) with `related_name="sub_users"`. This field can link users in a hierarchical relationship, though it is not heavily used in the current system logic. It exists to support potential user hierarchy features but does not affect authentication or permission checks.

### Flags

The model includes two boolean flags that control access:

- `is_active` — Determines whether the user can log in. Inactive users are rejected during authentication even if they provide correct credentials
- `is_staff` — Required for admin panel access. This flag is set to `True` for admin users and is checked alongside the role during admin login

### Django Integration

The model sets `USERNAME_FIELD = "phone"` to tell Django's authentication system that the phone number is the unique identifier for login. The `REQUIRED_FIELDS` list is empty because the phone is already the username field. The `objects` manager is a custom `UserManager` with:

- `create_user(phone, password, **extra_fields)` — Creates a regular user with the phone as identifier
- `create_superuser(phone, password, **extra_fields)` — Creates a superuser that must have `role="ADMIN"`, `is_superuser=True`, and `is_staff=True`

The model also overrides Django's default `groups` and `user_permissions` many-to-many fields with custom `related_name="custom_user_set"` to avoid conflicts with Django's built-in `User` model.

---

## User Roles

The system defines four **User Roles** through the `ROLE_CHOICES` on the model:

| Role | Purpose |
|------|---------|
| `ADMIN` | System administrator with access to the admin panel and all administrative APIs |
| `OWNER` | Shop owner — the highest non-admin role, can own and manage shops |
| `MANAGER` | Can manage shop operations but cannot own a shop |
| `STAFF` | Basic operational access for day-to-day tasks within a shop |

These roles are **global user-level roles**, meaning they define the user's permission level across the entire backend system. They are primarily used for **API-level permission checks** — certain endpoints require specific user roles before granting access.

---

## User Role vs Shop Role

It is important to distinguish between the **User Role** and the **Shop Role**, as they serve different purposes in the system.

### User Role

The User Role is assigned when the user is created. It defines what the user is capable of doing at the system level. For example:

- A user with role `STAFF` cannot become the Owner of any shop — their user-level role restricts them to staff-level operations only
- A user with role `OWNER` can be assigned as Owner, Manager, or Staff in different shops — their user role permits any shop-level assignment
- APIs that require owner-level credentials check against this user role to determine access

### Shop Role

The Shop Role is assigned when a user is linked to a specific shop through the `ShopUser` model (defined in the Shop module). Within a shop, a user may have roles such as Owner, Manager, or Staff. These roles determine what the user can do **inside that specific shop**.

A single user can have different shop roles in different shops. For example:

- User A (User Role: `OWNER`) is an **Owner** in Shop 1
- The same user is a **Manager** in Shop 2
- The same user is **Staff** in Shop 3

The key constraint is that the User Role must be at least as high as the Shop Role being assigned. If a user's User Role is `STAFF`, they cannot be assigned the `OWNER` shop role in any shop. Only if their User Role is `OWNER` can they be assigned as Owner in a shop.

Some APIs check permissions based on Shop Role (for example, "only the shop Owner can update privacy settings"), while other APIs check permissions based on User Role (for example, "only users with ADMIN role can access user management endpoints"). The system uses both levels of access control depending on the operation being performed.

---

## Authentication Workflow

The authentication flow works as follows:

1. The user opens the application and provides their **phone number and password**
2. The system normalizes the phone number to E.164 format
3. The system looks up the user by the normalized phone number
4. If the user exists and is active, it checks the password using Django's hashing system
5. If the password matches, the system generates a **JWT token pair** — an access token and a refresh token
6. The access token is returned in the response and used for subsequent API calls
7. The refresh token is used to obtain new access tokens when the current one expires

For admin login, the flow includes an additional check: the user must have `role="ADMIN"` and `is_staff=True`. The admin's refresh token is stored as an **HTTP-only cookie** for security, rather than being returned in the response body.

---

## CutoffJWTAuthentication – Token Invalidation

The system uses a custom authentication class called `CutoffJWTAuthentication` that extends the standard `JWTAuthentication` from Django REST Framework SimpleJWT. This custom class adds password-change-based token invalidation.

When a request is received with a JWT token, the `CutoffJWTAuthentication` class:

1. Extracts the `iat` (issued-at) timestamp and `user_id` from the token
2. Fetches the user from the database
3. Compares the token's `iat` with the user's `password_changed_at` timestamp
4. If `password_changed_at` exists and the token was issued **before** that timestamp, the token is rejected with an `AuthenticationFailed` error: "Token invalidated by password change"

This ensures that when a user changes their password, all previously issued tokens become invalid. The user must log in again to receive a new token. This is a security measure that prevents old sessions from continuing after a password change.

The v1 API version uses standard `JWTAuthentication` (without cutoff checking), while v3 and v4 use `CutoffJWTAuthentication` for enhanced security.

---

## Token Refresh

The system provides two token refresh endpoints:

### CutoffTokenRefreshView (Client-Side)

This view handles refresh token requests for regular users. Before issuing a new access token, it checks the `password_changed_at` field. If the refresh token was issued before the password was changed, the refresh is denied. This forces the user to log in again after a password change, even through the refresh flow.

### AdminTokenRefreshView (Admin-Side)

This view handles refresh token requests for admin users. The refresh token is read from an **HTTP-only cookie** named `refresh_token` rather than from the request body. This cookie-based approach provides additional security for admin sessions because the token is not accessible to JavaScript running in the browser.

---

## Admin-Side API Endpoints

The admin side provides APIs for managing all users in the system. All admin endpoints require the `IsAdminUserOnly` permission, which checks that the requesting user has `role="ADMIN"` and `is_staff=True`.

### Admin Login

- Endpoint: `POST /admin/login/`
- Permission: `AllowAny` (no authentication required)
- Rate limited: **10 requests per minute** per IP address

The admin login endpoint accepts a phone number and password. After validating the credentials and confirming the user has the `ADMIN` role and `is_staff=True`, it generates a JWT token pair. The access token is returned in the response body, while the refresh token is set as an **HTTP-only cookie** with the following security settings configured from the project settings:

- `httponly` — Prevents JavaScript access to the cookie
- `secure` — Cookie only sent over HTTPS
- `samesite` — Controls cross-site cookie behavior
- `max_age` — Cookie expiration duration
- `domain` and `path` — Cookie scope

### Admin Password Change

- Endpoint: `POST /admin/change-password/`
- Permission: `IsAdminUserOnly`

Allows the admin to change their own password. The endpoint validates the old password, ensures the new password is different from the current one, hashes the new password, and sets `password_changed_at` to the current timestamp. This invalidates all existing tokens for the admin, effectively logging them out from all devices.

### User CRUD Operations

The `UserViewSet` provides full CRUD for managing users:

**List Users**
- Endpoint: `GET /admin/users/`
- Supports search filtering by `full_name`, `last_name`, `email`, and `phone`
- Returns paginated results ordered by ID (newest first)
- Excludes superusers from the listing

**Retrieve User**
- Endpoint: `GET /admin/users/{id}/`
- Returns full user details including role, phone, name, email, and active status

**Create User**
- Endpoint: `POST /admin/users/`
- Creates a new user account with the provided phone, password, name, role, and optional email
- Phone numbers are normalized to E.164 format during validation
- The serializer handles various input formats: `+91XXXXXXXXXX`, `91XXXXXXXXXX`, `0XXXXXXXXXX`, or plain `XXXXXXXXXX`
- The password is hashed using Django's password hashing system before storage
- In v3, the `secondary_password` field is also required during creation

**Update User**
- Endpoint: `PATCH /admin/users/{id}/`
- Supports partial updates to any user field
- If the password is provided, it is hashed before saving

**Delete User**
- Endpoint: `DELETE /admin/users/{id}/`
- Soft-deletes the user (sets the `deleted` timestamp)
- Also deactivates related `ShopOwners` records from the legacy system

**Reset Password**
- Endpoint: `POST /admin/users/reset-password/`
- Allows the admin to reset any user's password without knowing the old password
- The new password is hashed and saved to the user record

---

## User-Side API Endpoints

The user-side endpoints serve the mobile and web applications used by shop Owners, Managers, and Staff.

### User Login

- Endpoint: `POST /login/`
- Permission: `AllowAny`

The user login endpoint accepts a phone number and password. The system performs the following steps:

1. Normalizes the phone number to E.164 format, handling various input formats
2. Looks up the user by the normalized phone number
3. Checks that the user is active (`is_active=True`)
4. Validates the password using Django's `check_password` method
5. Generates a JWT token pair (access + refresh)

In the v3 version, the login endpoint also accepts `device_token` and `platform` parameters. When provided, the system creates or updates a `DeviceToken` record for the user, which is used for Firebase Cloud Messaging (FCM) push notifications. The system deactivates any existing device tokens with the same FCM token (to prevent duplicate notifications) and creates a new active token entry linked to the user's shop.

### User Profile

- Endpoint: `GET /profile/`
- Permission: `IsAuthenticated`

The user profile endpoint is one of the most important endpoints in the system. It returns not just the user's basic information, but a comprehensive set of configuration data that determines how the application behaves for that user. The mobile application fetches this profile after login and uses the returned data to configure its entire interface.

The profile response includes:

**User Information**
- User ID, phone number, full name, last name, email, and role

**Active Shop Details**
- The profile includes details of the user's currently active shop (resolved using the `get_active_shop_id` utility from the Shop module)
- Shop ID, name, phone numbers, address, GST number, shop image URL, place, email, city, state, pincode, and terms and conditions
- Whether the user is associated with multiple shops (`multiple_shops` flag)

**Shop Role**
- The user's role within the active shop (Owner, Manager, or Staff), retrieved from the `ShopUser` record
- This is separate from the global User Role and determines shop-level permissions

**Subscription Information (v3/v4)**
- The active shop's subscription plan type and status
- List of features available under the subscription (from both the base plan and add-on subscriptions)
- Subscription expiry date and days remaining
- User-specific feature access (some users may have restricted access to certain subscription features)

**Password Settings (v3/v4)**
- The shop's feature access policies from `ShopFeaturePolicy`
- For each feature (ledger view, monthly gross, booking delete, booking edit, booking payment, transfer product, product deletion, product edit), the policy defines who must enter the shop password
- If a policy is not explicitly set for a feature, the system applies defaults:
  - `BOOKING_DELETE` — `ALL` (everyone needs password)
  - `LEDGER_VIEW` — `ALL`
  - `MONTHLY_GROSS_VIEW` — `ALL`
  - `BOOKING_EDIT` — `NONE` (no password required)
  - `BOOKING_PAYMENT` — `NONE`
  - `TRANSFER_PRODUCT` — `ALL`
  - `PRODUCT_DELETION` — `ALL`
  - `PRODUCT_EDIT` — `STAFF`

These settings are configured by the shop Owner through the Shop Privacy Settings API (in the Shop module). When Staff, Manager, or Owner users fetch their profile, these settings tell the mobile application which features to gate behind a password prompt.

**Shop Settings (v3/v4)**
- Operational settings configured for the shop from `ShopSettings`
- Includes `cooling_days` (number of cooling days between bookings), `default_action` (what the main action button does — ASK_EVERY_TIME, BOOKINGS, SALES, or CUSTOMIZATION), `search_client` (whether to search for existing clients), and `cooling_period_type` (whether the cooling period is applied before or after)
- Defaults are applied if no settings record exists: 0 cooling days, BOOKINGS action, no client search, "after" cooling period type

**Notification Status (v3/v4)**
- `has_active_notification` flag indicating whether the user has an active FCM device token for the current shop
- Used by the frontend to determine whether to prompt for notification permissions

### Change Password

- Endpoint: `POST /change-password/`
- Permission: `IsAuthenticated`

Allows the user to change their own password. The endpoint validates the old password, sets the new password (hashed), and saves the user record. An optional `logout_from_all_devices` parameter can be set to `true`, which:

- In v1: Blacklists all outstanding tokens for the user via SimpleJWT's `OutstandingToken` and `BlacklistedToken` models
- In v3: Sets `password_changed_at` to the current timestamp, causing all existing tokens to be invalidated through the `CutoffJWTAuthentication` mechanism

### Wallet Authentication

- Endpoint: `POST /wallet-login/`
- Permission: `IsAuthenticated`

The wallet authentication endpoint provides a secondary authentication step for accessing sensitive shop data like the ledger, financial reports, and gross revenue information. This is a **password gate** — the user is already logged in, but they must provide an additional password before the application unlocks protected sections.

The behavior differs between API versions:

- **v1**: Validates the user's `secondary_password` field (legacy behavior). The password is compared as plain text against the stored value
- **v3**: Validates against the **shop's password** (`shop.password`). The system resolves the active shop, verifies the user has access to that shop through a `ShopUser` record, and then checks the provided password against the shop-level password. Returns the shop ID, shop name, and user's shop role on success

This evolution reflects the shift from user-level secondary passwords to shop-level password protection, where the shop Owner controls the password that gates access to sensitive data for all users within that shop.

### Update Secondary Password

- Endpoint: `POST /update-secondary-password/`
- Permission: `IsAuthenticated`

This endpoint also evolved between API versions:

- **v1**: Updates the user's `secondary_password` field. The serializer validates that the old secondary password matches, that the new password is different from the main login password, and that the new password is different from the old secondary password
- **v3**: Updates the **shop's password** (`shop.password`). Only users with the `OWNER` shop role can update the shop password. The endpoint validates password requirements (6–50 characters, no spaces, must be different from the current shop password), updates the shop's `password_changed_at` timestamp, and returns confirmation with the shop ID and name

---

## Phone Number Normalization

Phone number normalization is used consistently throughout the authentication module to ensure all phone numbers are stored and compared in the same format. The normalization utility supports the following input formats:

| Input Format | Example | Normalized Output |
|-------------|---------|-------------------|
| E.164 with `+91` | `+919876543210` | `+919876543210` |
| With `91` prefix | `919876543210` | `+919876543210` |
| With leading `0` | `09876543210` | `+919876543210` |
| Plain 10 digits | `9876543210` | `+919876543210` |

The normalization uses the `phonenumbers` library with India (`IN`) as the default region. Invalid phone numbers (wrong digit count, non-numeric characters) are rejected with a `ValueError`. This normalization is applied during:

- User login (before looking up the user)
- User creation (in the serializer validation)
- User updates (when phone number is changed)

---

## Permissions

### IsAdminUserOnly

The `IsAdminUserOnly` permission class restricts access to users who meet all three criteria:

- `is_authenticated` — The request has a valid JWT token
- `role == "ADMIN"` — The user's global role is Admin
- `is_staff == True` — The Django staff flag is set

This permission is used on all admin-side endpoints (user CRUD, admin password change, password reset).

### SubscriptionActivePermission (from Shop Module)

Many user-side endpoints across the application (not just authentication) also require `SubscriptionActivePermission`, which checks that the user's active shop has a valid subscription. This permission is not defined in the authentication module but is commonly used alongside authentication checks in other modules.

---

## Admin Panel Configuration

The Django admin panel includes a `UserAdmin` class that provides a management interface for user accounts:

- **List display**: ID, phone, full name, email, role, is_active, is_staff, is_superuser, deleted status, password (hashed)
- **Search by**: phone number, full name, email
- **Filters**: role, is_active, is_staff, is_superuser, deleted status
- **Fieldsets**: Organized into sections — phone/password, personal info, permissions, role info, and important dates

The admin class extends `CommonSafeDeleteAdmin`, which provides a "Hard delete selected objects" bulk action for permanently removing soft-deleted records when necessary.

---

## API Versioning

The authentication module has evolved through multiple API versions:

| Version | Key Characteristics |
|---------|-------------------|
| **v1** | Original API using `JWTAuthentication`. Basic CRUD, login, profile (with shop details), change password, wallet login with `secondary_password`, update secondary password on user model. Rate-limited admin login. |
| **v3** | Enhanced with `CutoffJWTAuthentication` and `StandardResponseMixin` for consistent responses. Login accepts `device_token` and `platform` for FCM integration. Profile returns subscription data, password settings, shop settings, user features, and notification status. Wallet login validates against **shop password** instead of user's secondary password. Update secondary password now updates **shop password** with owner validation. `secondary_password` required during user creation. |
| **v4** | Minimal version — only the `UserProfileView` endpoint, using `CutoffJWTAuthentication`. Same enhanced profile response as v3 (subscription, password settings, shop settings). No login, CRUD, or password endpoints. |

### URL Configuration

| URL Pattern | v1 | v3 | v4 | Description |
|-------------|----|----|-----|-------------|
| `admin/login/` | ✓ | ✓ | — | Admin login (rate limited, cookie-based refresh) |
| `admin/users/` | ✓ | ✓ | — | User CRUD (list, create, retrieve, update, delete) |
| `admin/change-password/` | ✓ | ✓ | — | Admin password change |
| `admin/users/reset-password/` | ✓ | ✓ | — | Admin resets user password |
| `login/` | ✓ | ✓ | — | User login (phone + password → JWT) |
| `profile/` | ✓ | ✓ | ✓ | User profile with shop and configuration data |
| `change-password/` | ✓ | ✓ | — | User password change |
| `wallet-login/` | ✓ | ✓ | — | Secondary authentication for sensitive data |
| `update-secondary-password/` | ✓ | ✓ | — | Update wallet/shop password |

Token refresh endpoints are registered at the project level rather than within the authentication module's URL configuration:

- `api/token/refresh/` — `CutoffTokenRefreshView` for client-side token refresh (with password cutoff checking)
- `api/admin-token/refresh/` — `AdminTokenRefreshView` for admin token refresh (reads refresh token from HTTP-only cookie)

---

## Apple App Site Association

The authentication module also serves the **Apple App Site Association** file, which is used by iOS to associate the application's domain with the native iOS app. This file is served as a static JSON response at two paths:

- `/.well-known/apple-app-site-association`
- `/apple-app-site-association`

This enables universal links (deep links) for the iOS version of the application.

---

## Module Relationships

The Authentication module connects to other modules in the following ways:

- **Shop** — Users are linked to shops through the `ShopUser` model. The profile endpoint resolves the active shop and returns shop-level settings and policies
- **Subscriptions** — The profile endpoint includes subscription plan details, feature lists, and expiry information from the `ShopSubscription` and `ShopFeatureSubscription` models
- **Notifications** — The v3 login endpoint manages FCM device tokens for push notifications. The profile reports active notification status
- **Booking, Sales, Services, Expenses** — All these modules use `CutoffJWTAuthentication` from this module's middleware for request authentication
- **WhatsApp** — Phone numbers stored in E.164 format enable direct WhatsApp messaging integration
