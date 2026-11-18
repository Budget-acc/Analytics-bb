# Subscription System Documentation

---

## Overview

The **Subscription Module** manages subscription plans, features, bundles, pricing rules, feature limits, and feature access for shops. It provides a flexible system where:

- Shops subscribe to plans
- Plans automatically provide a set of base features
- Shops can purchase extra features individually or in bundles
- Feature access can be controlled per user role and per individual user
- Feature usage can be limited, tracked, and overridden per shop

This system allows the platform to **enable or disable functionality dynamically** based on a shop's subscription status. Rather than hardcoding which features are available to which shop, the subscription system makes all of this configurable at runtime through the admin panel — no code changes are needed to give or remove a feature from a shop.

---

## Core Responsibilities

The subscription module is responsible for:

- Creating and managing subscription plans (Basic, Premium, Enterprise)
- Managing system features and their usage types
- Creating feature bundles for grouped access
- Assigning subscription plans to shops
- Assigning individual features to shops outside of a plan
- Managing feature pricing and discounts per shop
- Controlling feature access per user role (platform-wide defaults)
- Controlling feature access per individual user within a specific shop
- Enforcing feature usage limits based on the assigned plan
- Tracking feature consumption per shop per billing period
- Applying custom usage limit adjustments for specific shops

---

## 1. Subscription Plan

The **SubscriptionPlan** model represents the base subscription tiers available in the system — such as Basic, Premium, and Enterprise. Each plan acts as a **template** that defines what a shop receives when they subscribe, including:

- How many users the shop can have
- How many products they can manage
- How many bookings they can create per month
- How long the subscription is valid
- Which features are included by default

### Plan Types

| Type | Description |
|------|-------------|
| `BASIC` | Entry-level plan with core features and lower limits |
| `PREMIUM` | Mid-tier plan with more features and higher limits |
| `ENTERPRISE` | Top-tier plan with maximum features and very high or unlimited limits |

### Plan Limits

| Field | Description |
|-------|-------------|
| `max_users` | Maximum number of staff/user accounts the shop can create |
| `max_products` | Maximum number of product listings allowed |
| `max_bookings_per_month` | Monthly booking creation limit |
| `duration_days` | How many days the subscription remains valid after activation |
| `base_price` | Base subscription price for the plan |

### Plan Duration

The `duration_days` field controls the subscription validity period:

- A plan with `duration_days = 30` is a **monthly plan**
- A plan with `duration_days = 365` is an **annual plan**
- Any custom duration can be configured without code changes

### Included Features

The most important part of a subscription plan is the `included_features` field:

- It is a **many-to-many relationship** with the `Feature` model
- A single plan can include any number of features
- Every feature included here **automatically becomes available** to any shop that subscribes to that plan
- This means plan upgrades can include new features simply by adding them to the plan's `included_features` in the admin panel

### Example Plan Comparison

| Plan | Max Users | Max Products | Bookings/Month |
|------|-----------|--------------|----------------|
| Basic | 1 | 100 | 50 |
| Premium | 5 | 1,000 | 500 |
| Enterprise | Unlimited | Unlimited | Unlimited |

---

## 2. Feature

The **Feature** model represents a **specific piece of functionality** in the system. Instead of features being hardcoded into the application, each feature is a database record that can be enabled or disabled for any shop independently.

### Why Features Are Database Records

This design decision is intentional and important. By making features database records instead of hardcoded flags, the system gains these capabilities:

- A new feature can be created in the admin panel and assigned to specific shops without any code deployment
- A feature can be removed from a shop instantly by an admin without touching code
- Feature pricing, usage limits, and permissions can all be adjusted at runtime
- The same feature can have different limits on different plans

### Feature Code

Each feature has a unique `code` field that is the **bridge between the database record and the application logic**:

- The code is a short string like `"WHATSAPP_MESSAGING"` or `"ADVANCED_REPORTS"`
- Anywhere in the application where a permission check is needed, the code is passed to the `has_feature_access()` helper function
- If the feature code is not in the user's accessible feature list, the functionality is blocked

Example usage in a view:
```python
has_feature_access(shop_user, "WHATSAPP_MESSAGING")
```

### Feature Types

| Type | Description |
|------|-------------|
| `CORE` | Fundamental platform functionality — included in all plans by default |
| `ADDON` | Optional features that shops can purchase separately |
| `PREMIUM` | Advanced features available only in higher-tier plans |

### Feature Usage Types

The `usage_type` field defines **how the feature's consumption is measured and limited**:

| Usage Type | Meaning | Example |
|------------|---------|---------|
| `NONE` | No usage limit — unlimited use | Product listing, booking creation |
| `COUNT` | Tracked by count of individual operations | WhatsApp messages sent, PDF exports |
| `STORAGE` | Tracked by storage consumed | File uploads (planned for future) |
| `TIME` | Tracked by time used | API access minutes (planned for future) |

Only features with `usage_type = COUNT` are linked to `FeatureUsagePolicy` records. Only count-based features need monthly or daily limits enforced — unlimited features (`NONE`) require no tracking.

### Feature Dependencies

The `requires_features` field is a **self-referential many-to-many** relationship that allows defining feature prerequisites:

- For example, an "Advanced Reports" feature might require the "Basic Reports" feature to be active first
- This ensures prerequisite features are always available before a dependent feature can be used
- The system can validate these dependencies when assigning features to a shop

### Feature Permissions Field

The `permissions` field is a flexible **JSON object** that can store any additional configuration or capability flags for that feature. Different features may need different configuration structures, so JSON avoids the need for separate configuration tables.

---

## 3. Shop Subscription

The **ShopSubscription** model is the **central record that assigns an active subscription plan to a shop**. It is the most important model in the entire subscription system because it is the entry point for all feature resolution.

### One Subscription Per Shop

Each shop has at most **one `ShopSubscription` record**, enforced by the `OneToOneField` relationship with the Shop model. This ensures there is never any ambiguity about which plan a shop is currently on.

### What a Shop Subscription Contains

A shop's subscription is not just a pointer to a plan. It connects three different sources of features:

- **Plan features** — All features in the assigned `SubscriptionPlan.included_features`
- **Add-on features** — Individual features purchased separately, linked through `ShopFeatureSubscription`

A shop's actual feature set is the **union of all three sources**, deduplicated.

### Subscription Status

| Status | Meaning |
|--------|---------|
| `ACTIVE` | Subscription is valid and all features are accessible |
| `SUSPENDED` | Temporarily disabled by admin — all features are blocked until restored |
| `EXPIRED` | Subscription period has ended — features no longer accessible |
| `CANCELLED` | Subscription manually cancelled by admin |

### Payment Fields

The subscription also stores financial tracking fields:

- `total_amount` — Total amount charged for the subscription
- `payment_status` — Whether payment has been received (boolean)
- `auto_renew` — Whether the subscription should automatically renew when it expires

### How Feature Resolution Works (get_all_features)

The `get_all_features()` method on the model brings all three feature sources together:

1. Start with all features from `SubscriptionPlan.included_features`
2. Add features from all active `ShopFeatureSubscription` records (add-on features)
4. **Merge and deduplicate** — the same feature may appear in multiple sources but is counted only once
5. Return the final list

The `get_feature_codes()` method converts this to a simple list of code strings (e.g., `["WHATSAPP_MESSAGING", "ADVANCED_REPORTS"]`), which is used for quick permission checks throughout the application.

This means: even if a feature is **not** in the base plan, a shop can still access it if they have purchased it individually or as part of a bundle.

---

## 4. Shop Feature Subscription

The **ShopFeatureSubscription** model is the **intermediate model that links individual features to a shop's subscription**. It is used when a shop wants to purchase a specific feature that is not included in their base plan.

### Why This Model Exists

The base subscription plan includes a fixed set of features. But shops may want to purchase additional features beyond their plan — for example, a Basic plan shop might want to add WhatsApp Messaging without upgrading to Premium. The `ShopFeatureSubscription` model makes this possible by linking any feature directly to any shop's subscription.

### Custom Pricing Per Shop

This model stores the **actual price paid by the shop** for that feature, which may differ from the feature's global `base_price`. This allows for:

- Negotiated pricing for large clients
- Loyalty discounts for long-term shops
- Promotional pricing for new shops
- Bulk purchase discounts

### Audit Trail Fields

The model maintains a complete pricing audit trail:

| Field | Description |
|-------|-------------|
| `price_paid` | Actual amount this shop pays for the feature |
| `discount_amount` | Discount amount deducted from base price |
| `discount_reason` | Why the discount was given (loyalty, bulk purchase, promotional, etc.) |
| `base_price_at_purchase` | The feature's base price at time of purchase — preserved even if base price changes later |

The `base_price_at_purchase` field is particularly important — it means the system always knows what the market rate was when the shop purchased the feature, which is essential for billing reconciliation and dispute resolution.

### Validity

- `start_date` and `end_date` define the window during which the feature is active
- The `is_active` flag provides an additional manual control switch
- A feature subscription is only counted in `get_all_features()` if `is_active = True`

---

## 5. User Subscription Access

The **UserSubscriptionAccess** model provides **fine-grained feature access control at the individual user level** within a shop. This model answers the question: even if the shop has a feature, should every user inside that shop be able to use it?

### Why User-Level Control Is Needed

In a shop, different users have different roles and responsibilities:

- The shop **Owner** should have access to all features
- A **Manager** might not need access to financial reports or user management features
- **Staff** members should only see features relevant to their day-to-day work

The `UserSubscriptionAccess` model allows the shop owner or admin to configure exactly this, per user.

### How It Works

This model is linked **one-to-one** with a `ShopUser` record. It stores two types of feature overrides:

- `allowed_features` — Extra features added to this specific user beyond what the shop has
- `restricted_features` — Features blocked for this user even if the shop has them active

Beyond feature overrides, the model also stores **operational limits**:

| Field | Description |
|-------|-------------|
| `allowed_features` | Extra features granted specifically to this user |
| `restricted_features` | Features this user cannot access despite the shop having them |
| `max_bookings_per_day` | Cap on how many bookings this user can create per day |
| `can_view_analytics` | Whether this user can access analytics dashboards |
| `can_manage_users` | Whether this user can add or manage other shop users |

### The get_accessible_features() Method

This method computes the **final feature set for a specific user** by combining three things:

1. Start with the shop's full feature set (from `get_all_features()` on the ShopSubscription)
2. Add any features in `allowed_features` (user-level extras)
3. **Remove** any features in `restricted_features` (user-level blocks)

The result is the exact list of features this specific user can access — it is the most specific and final level of feature resolution.

### Example Role Access

| Role | Typical Access Configuration |
|------|------------------------------|
| Owner | All shop features, no restrictions, can manage users and view analytics |
| Manager | Most features, possibly restricted from user management and financial exports |
| Staff | Core booking and product features only, analytics and admin features blocked |

---

## 6. User Feature — Platform-Wide Role Defaults

The **UserFeature** model defines the **platform-wide default feature access policy per user role**. It acts as a system-wide baseline that applies across all shops before any individual overrides are applied.

### What It Does

Each record in `UserFeature` connects a **role** (Owner, Manager, Staff) to a **feature**. This means:

- A feature linked to `OWNER` is accessible to all owners across all shops by default
- A feature linked to `MANAGER` is accessible to all managers by default
- Features not linked to a role are not accessible to users of that role by default

### Role Options

| Role | Description |
|------|-------------|
| `OWNER` | Full access — typically has all features available |
| `MANAGER` | Intermediate access — has most features, some advanced ones may be excluded |
| `STAFF` | Minimal access — only day-to-day operational features |

### Global vs Local Scope

The key distinction between `UserFeature` and `ShopUserFeature` is **scope**:

- `UserFeature` is **global** — it applies everywhere across the entire platform for every shop
- `ShopUserFeature` is **local** — it applies only within a specific shop for a specific user

So `UserFeature` defines the default, and `ShopUserFeature` can override that default for individual users within individual shops.

---

## 7. Shop User Feature — Per-User Overrides Within a Shop

The **ShopUserFeature** model allows **shop-level custom feature overrides for individual users**. While `UserFeature` defines platform-wide role defaults, `ShopUserFeature` lets a specific shop grant or block a feature for a specific user within that shop only.

### The granted Field

The `granted` boolean field controls the **direction of the override**:

| `granted` Value | Effect |
|-----------------|--------|
| `True` | User gains access to the feature — even if their role default does not allow it |
| `False` | User is denied access to the feature — even if their role default would allow it |

### Why This Two-Way Override Matters

This two-directional capability makes the access control system very flexible:

- A shop owner can **promote a staff member** to access specific sensitive features (e.g., giving a trusted staff member access to reports)
- A shop owner can **restrict a manager** from features they would otherwise have based on their role (e.g., blocking a manager from deleting bookings)
- Each shop can have completely different user permission configurations without affecting other shops

### Example

```
Platform default: Manager role has access to "Product Transfer" feature

ShopUserFeature record:
  shop_user = Manager at Shop A
  feature   = Product Transfer
  granted   = False

Result: This specific manager at Shop A CANNOT access Product Transfer,
        even though all other managers on the platform can.
```

---

## 8. Feature Usage Policy

The **FeatureUsagePolicy** model defines the **numeric usage limits for count-based features per subscription plan**. This is the model that answers: "how many times can a shop use this feature per billing period, based on their plan?"

### Why This Is a Separate Model

The `Feature` model itself only defines what a feature is and what type of usage tracking it uses. The actual numeric limits are defined here, separately. This separation is intentional and important because:

- A single feature can have **different limits on different plans**
- Limits can be changed without modifying the feature itself
- New plans can define their own limits for the same feature without any code changes
- Administrators can adjust limits from the admin panel at any time

### How It Works

Each `FeatureUsagePolicy` record links one `SubscriptionPlan` to one `Feature` and defines:

- `limit` — The maximum number of times the feature can be used in one billing period
- `reset_cycle` — How often the usage counter is reset

Because of the `unique_together = ("plan", "feature")` constraint, each plan-feature combination has exactly one policy record.

### Example — WhatsApp Messaging Limits by Plan

| Subscription Plan | Monthly Message Limit |
|-------------------|-----------------------|
| Basic | 100 messages |
| Premium | 500 messages |
| Enterprise | 2,000 messages |

These three limits are three separate `FeatureUsagePolicy` records — all pointing to the same WhatsApp feature, but each attached to a different plan.

### Reset Cycle Options

| Cycle | Description |
|-------|-------------|
| `MONTHLY` | Usage counter resets at the start of each billing month |
| `DAILY` | Usage counter resets each day at midnight |
| `LIFETIME` | No reset — total lifetime usage is tracked and never cleared |

### Only Applies to COUNT Features

`FeatureUsagePolicy` records are only created for features where `usage_type = COUNT`. Features with `usage_type = NONE` (unlimited) never need a policy record because there is nothing to limit.

---

## 9. Shop Feature Limit

The **ShopFeatureLimit** model allows administrators to apply **custom usage limit adjustments for specific shops**, overriding the plan-level defaults set in `FeatureUsagePolicy`.

### How It Works

The `extra_limit` field is a signed integer that adjusts the shop's effective limit:

- A **positive value** adds more usage capacity on top of the plan's default
- A **negative value** reduces the shop's limit below the plan default

This is useful for special commercial arrangements:

- A shop that negotiated an extended WhatsApp quota gets a positive `extra_limit`
- A shop on a restricted or trial agreement gets a negative `extra_limit`
- Temporary promotions can be set with a `valid_until` date so they expire automatically

### Control Fields

- `valid_until` — Date after which the limit adjustment expires automatically
- `is_active` — Manual on/off switch for the adjustment
- `reason` — Internal note explaining why the adjustment was made (audit record)

### Example Adjustments

| Shop | Feature | Plan Default | Extra Limit | Effective Limit |
|------|---------|--------------|-------------|-----------------|
| Shop A | WhatsApp | 500/month | +1,000 | 1,500/month |
| Shop B | WhatsApp | 500/month | -300 | 200/month |

### Effective Limit Calculation

When checking whether a shop can use a feature, the system calculates:

```
Effective Limit = FeatureUsagePolicy.limit + ShopFeatureLimit.extra_limit
```

If no `ShopFeatureLimit` record exists for that shop and feature, the plan default is used as-is.

---

## 10. Shop Feature Usage

The **ShopFeatureUsage** model **tracks how much of a count-based feature a shop has consumed** during a given billing period. It is the real-time usage counter that is checked before every feature action.

### How It Works

Each record stores:

- `shop` — Which shop's usage is being tracked
- `feature` — Which feature is being consumed
- `used` — How many times the feature has been used in the current period
- `period_start` — Start of the current billing window
- `period_end` — End of the current billing window

When a shop performs an action that consumes a feature — for example, sending a WhatsApp message — the system:

1. Finds the current `ShopFeatureUsage` record for that shop and feature
2. **Checks** whether `used + 1` would exceed the effective limit (plan limit + any `ShopFeatureLimit` adjustment)
3. If within limit — allows the action and increments `used` by 1
4. If over limit — **blocks the action** and returns an error

### Preventing Duplicate Records

The `unique_together` constraint on `(shop, feature, period_start)` ensures there is **only ever one usage record per shop per feature per billing period**. This prevents duplicate tracking records from being created, which could cause incorrect limit enforcement.

### When Usage Resets

When the `reset_cycle` in the linked `FeatureUsagePolicy` triggers (monthly, daily, or never for lifetime), a **new `ShopFeatureUsage` record** is created for the new period with `used = 0`. The old record is retained for historical reporting.

---

## Feature Access Resolution — Full Flow

When the system needs to determine whether a user can access a specific feature, it follows this complete resolution sequence. This flow runs every time `has_feature_access()` is called.

### Step-by-Step Resolution

1. **Check shop subscription exists** — If the shop has no `ShopSubscription` record, access is denied immediately

2. **Check subscription is active** — The subscription must have `status = ACTIVE` and `end_date` must be in the future. If either condition fails, access is denied

3. **Collect shop-level features from three sources:**
   - Features from `SubscriptionPlan.included_features` (base plan features)
   - Features from active `ShopFeatureSubscription` records (individual add-ons)
   - All three sets are merged and deduplicated

4. **Apply role-based defaults from `UserFeature`** — Features that the user's role (Owner / Manager / Staff) is allowed to access by platform-wide default

5. **Apply per-user shop overrides from `ShopUserFeature`:**
   - Records with `granted = True` — add those features to the user's accessible set
   - Records with `granted = False` — remove those features from the user's accessible set

6. **Apply user-level access control from `UserSubscriptionAccess`:**
   - Add features from `allowed_features`
   - Remove features from `restricted_features`

7. **Return the final resolved feature set** — if the requested feature code is in this set, access is granted

### has_feature_access() Helper Function

The system exposes a helper function that is called throughout the application wherever a permission check is needed in views, APIs, or background tasks:

```python
has_feature_access(shop_user, feature_code)
```

This function:

- Checks that the shop has a subscription and that it is active
- Resolves the user's accessible features through `UserSubscriptionAccess.get_accessible_features()`
- Returns `True` if the feature code is in the resolved list
- Returns `False` immediately if the shop has no subscription or the subscription is not active
- Catches all exceptions and returns `False` safely — a permission check should never crash the application

### Access Decision Hierarchy (Most Specific Wins)

```
Platform-wide role defaults (UserFeature)
      ↓
Shop-level user overrides (ShopUserFeature)
      ↓
User-level grants and restrictions (UserSubscriptionAccess)
      ↓
Final access decision
```

---

## Subscription APIs

Most subscription operations are handled through **Admin APIs or the Django Admin panel**. These are not customer-facing APIs — they are used by platform administrators to manage shop subscriptions.

### Subscription Plan APIs

CRUD operations for managing subscription plan records:

- **Create Plan** — Define a new subscription tier with limits and included features
- **Update Plan** — Modify limits, pricing, duration, or which features are included
- **Delete Plan** — Remove plans that are no longer offered
- **List Plans** — View all active subscription plans

### Feature APIs

CRUD operations for managing feature records:

- **Create Feature** — Register a new feature with a unique code, type, and usage type
- **Update Feature** — Change pricing, usage type, or feature configuration
- **Deactivate Feature** — Disable a feature so it is no longer available to any shop
- **List Features** — View all features and their current configuration

### Shop Subscription Management APIs

These APIs allow administrators to manage a shop's subscription:

- **Assign Plan** — Link a subscription plan to a shop (creates a `ShopSubscription` record)
- **Upgrade / Downgrade Plan** — Change the plan linked to a shop's subscription
- **Cancel Subscription** — Set the subscription status to `CANCELLED`
- **Suspend Subscription** — Temporarily block all features for a shop
- **Add Extra Feature Limits** — Create `ShopFeatureLimit` records for specific shops

### Feature Assignment APIs

These APIs manage individual feature assignments to shops outside of their current plan:

- **Add Feature to Shop** — Create a `ShopFeatureSubscription` record with custom pricing
- **Remove Feature from Shop** — Deactivate a `ShopFeatureSubscription` record
- **Update Feature Limit** — Modify the `extra_limit` on a `ShopFeatureLimit` record

### Shop Subscription Details API

This API returns the complete subscription configuration for a shop:

- The active plan and its details
- All enabled features (from plan + add-ons + bundles combined)
- Current usage limits and any custom adjustments
- Subscription status and validity dates

This is the **primary API used by the frontend** to determine which features to show or hide for a given shop and user. Every time a shop's interface loads, it uses this API to build the feature-gated UI.

---

## Model Summary

| Model | Purpose |
|-------|---------|
| `SubscriptionPlan` | Defines subscription tiers — limits, duration, base price, and included features |
| `Feature` | Represents an individual system feature — code, type, usage type, and dependencies |
| `ShopSubscription` | The active subscription assigned to a shop — the entry point for all feature resolution |
| `ShopFeatureSubscription` | Individual feature add-ons purchased by a shop with custom per-shop pricing |
| `UserSubscriptionAccess` | User-level feature grants, restrictions, booking limits, and operational flags |
| `UserFeature` | Platform-wide default feature access policy per role (Owner / Manager / Staff) |
| `ShopUserFeature` | Per-user feature overrides (grant or block) within a specific shop |
| `FeatureUsagePolicy` | Numeric usage limits per feature per plan with reset cycle configuration |
| `ShopFeatureLimit` | Custom usage limit adjustments (+/-) for specific shops, with expiry and audit |
| `ShopFeatureUsage` | Real-time tracking of feature consumption per shop per billing period |
