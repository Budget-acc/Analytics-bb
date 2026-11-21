from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from rest_framework.permissions import IsAuthenticated
from ERP.utils.mixins import StandardResponseMixin
from rest_framework.views import APIView
from rest_framework import status
from django.conf import settings
from django.utils import timezone
from authentication.api.v5.serializers import UserProfileSerializer
from authentication.models import User
from shop.api.v3.utils.shop import get_active_shop_id
from shop.models import Shop, ShopUser, FeatureKey, PasswordRequirement, ShopFeaturePolicy
from subscriptions.models import Feature, ShopFeatureSubscription, get_feature_limit_status
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers
import logging


logger = logging.getLogger("api_requests")


class UserProfileView(APIView, StandardResponseMixin):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    DEFAULT_PASSWORD_SETTINGS = {
        "BOOKING_DELETE": "ALL",
        "LEDGER_VIEW": "ALL",
        "MONTHLY_GROSS_VIEW": "ALL",
        "BOOKING_EDIT": "NONE",
        "BOOKING_PAYMENT": "NONE",
        "TRANSFER_PRODUCT": "ALL",
        "PRODUCT_DELETION": "ALL",
        "PRODUCT_EDIT": "STAFF",
        "SHOP_PERFORMANCE": "ALL"
    }

    DEFAULT_SHOP_SETTINGS = {
        "cooling_days": 0,
        "default_action": "BOOKINGS",
        "cooling_period_type": "after",
        "printer_preference": "ASK_EVERY_TIME",
    }

    # Features usable without shop-level access (UI shows them regardless of shop).
    NON_SHOP_LEVEL_FEATURE_CODES = {"MARKETPLACE"}

    @staticmethod
    def _format_feature_date(value):
        """'DD-MM-YYYY HH:MM:SS' in local time, or None."""
        return timezone.localtime(value).strftime("%d-%m-%Y %H:%M:%S") if value else None

    @extend_schema(
        tags=["User Management V5"],
        summary="Retrieve authenticated user profile",
        description=(
            "Returns the full profile of the currently authenticated user, including personal "
            "details, the active shop's information, subscription plan and features, "
            "shop-level password policies, shop settings, public access details, and the "
            "shop's GST tax configuration.\n\n"
            "The active shop is resolved from the JWT token's shop context. "
            "All subscription feature codes, expiry dates, and remaining days are included "
            "when a subscription is active. Password settings default to system defaults when "
            "no shop-level policy is configured.\n\n"
            "**V5 changes from V4**: removes duplicate shop fields that were previously "
            "returned both at the top level and inside `shop` (they now live only inside "
            "`shop`); `password_settings` is now a list of descriptive objects instead of a "
            "flat key-value map; adds `organization_public_access` (public slug/token of the "
            "shop's parent organization, if any) and `shop.tax_configuration` (the shop's GST "
            "configuration, if any - nested inside `shop`, including the tax configuration "
            "row's own `id`).\n\n"
            "`ui_feature_access` lists every active, non-deleted feature from the global Feature "
            "table (not filtered by the active shop), for frontend show/hide control."
        ),
        responses={
            200: inline_serializer(
                name="UserProfileV5Response",
                fields={
                    "status": serializers.BooleanField(
                        help_text="True when the request succeeds."
                    ),
                    "message": serializers.CharField(
                        help_text="Human-readable success message."
                    ),
                    "dev_message": serializers.CharField(
                        help_text="Developer-facing diagnostic message with user and shop IDs."
                    ),
                    "data": inline_serializer(
                        name="UserProfileV5Data",
                        fields={
                            "id": serializers.IntegerField(
                                help_text="Purpose: Unique identifier for the user.\nFormat: Integer."
                            ),
                            "phone": serializers.CharField(
                                help_text="Purpose: Primary phone number of the user.\nFormat: String (E.164 or local format)."
                            ),
                            "full_name": serializers.CharField(
                                help_text="Purpose: Display name of the user.\nFormat: String."
                            ),
                            "last_name": serializers.CharField(
                                help_text="Purpose: Last name of the user.\nFormat: String. May be null."
                            ),
                            "email": serializers.CharField(
                                help_text="Purpose: Email address of the user.\nFormat: String. May be null."
                            ),
                            "role": serializers.CharField(
                                help_text="Purpose: Global account role of the user (e.g. OWNER, STAFF).\nFormat: String."
                            ),
                            "multiple_shops": serializers.BooleanField(
                                help_text="Purpose: Indicates whether the user is associated with more than one shop.\nFormat: Boolean."
                            ),
                            "has_active_notification": serializers.BooleanField(
                                help_text="Purpose: True if the user has an active FCM device token registered for the active shop.\nFormat: Boolean."
                            ),
                            "subscription": inline_serializer(
                                name="SubscriptionV5Info",
                                fields={
                                    "plan": serializers.CharField(
                                        help_text="Subscription plan type (e.g., 'basic', 'pro'). 'none' if no plan."
                                    ),
                                    "status": serializers.CharField(
                                        help_text="Subscription status (e.g., 'active', 'inactive')."
                                    ),
                                    "features_details": serializers.ListField(
                                        child=inline_serializer(
                                            name="FeatureDetailV5",
                                            fields={
                                                "code": serializers.CharField(help_text="Feature code identifier."),
                                                "name": serializers.CharField(help_text="Human-readable feature name."),
                                                "status": serializers.CharField(help_text="'active' if valid, 'expired' if end date has passed."),
                                                "limit_exceeded": serializers.BooleanField(
                                                    allow_null=True,
                                                    help_text=(
                                                        "True/false for usage-metered features (e.g. WhatsApp messages) - "
                                                        "whether current usage has reached the plan limit plus any active "
                                                        "extra quota. Null for features with no usage limit."
                                                    ),
                                                ),
                                                "start_date": serializers.CharField(
                                                    allow_null=True,
                                                    help_text=(
                                                        "From ShopFeatureSubscription. Format: DD-MM-YYYY HH:MM:SS (IST). "
                                                        "Null when the feature has no ShopFeatureSubscription row "
                                                        "(e.g. included in the base plan only)."
                                                    ),
                                                ),
                                                "end_date": serializers.CharField(
                                                    allow_null=True,
                                                    help_text=(
                                                        "From ShopFeatureSubscription. Format: DD-MM-YYYY HH:MM:SS (IST). "
                                                        "Null when the feature has no ShopFeatureSubscription row "
                                                        "(e.g. included in the base plan only)."
                                                    ),
                                                ),
                                            },
                                        ),
                                        help_text="All assigned features with their current validity status.",
                                    ),
                                    "expiry_date": serializers.CharField(
                                        help_text="Subscription expiry date in DD-MM-YYYY format. Present only if subscription has an end date."
                                    ),
                                    "days_remaining": serializers.IntegerField(
                                        help_text="Number of days remaining until subscription expiry. Present only if expiry is in the future."
                                    ),
                                },
                            ),
                            "password_settings": serializers.ListField(
                                child=inline_serializer(
                                    name="PasswordSettingV5",
                                    fields={
                                        "setting_name": serializers.CharField(
                                            help_text="Purpose: The feature key this password policy applies to (e.g. LEDGER_VIEW).\nFormat: String."
                                        ),
                                        "requires_password_for": serializers.CharField(
                                            help_text="Purpose: Which role(s) must re-enter a password to use this feature.\nFormat: One of ALL, NONE, STAFF, MANAGER_AND_STAFF."
                                        ),
                                        "description": serializers.CharField(
                                            help_text="Purpose: Human-readable description of the feature.\nFormat: String."
                                        ),
                                    },
                                ),
                                help_text=(
                                    "Purpose: List of password requirement policies, one per feature key.\n"
                                    "Format: Array of objects, replacing V4's flat key-value map."
                                ),
                            ),
                            "shop_settings": inline_serializer(
                                name="ShopSettingsV5",
                                fields={
                                    "cooling_days": serializers.IntegerField(
                                        help_text="Number of cooling days applied after a booking."
                                    ),
                                    "default_action": serializers.CharField(
                                        help_text="Default landing action for the shop (e.g., 'BOOKINGS')."
                                    ),
                                    "cooling_period_type": serializers.CharField(
                                        help_text="Type of cooling period calculation ('after' or other variants)."
                                    ),
                                    "printer_preference": serializers.CharField(
                                        help_text="Which document to print by default (ASK_EVERY_TIME, INVOICE_PDF, or RECEIPT)."
                                    ),
                                },
                            ),
                            "shop": inline_serializer(
                                name="ShopDetailV5",
                                fields={
                                    "id": serializers.IntegerField(help_text="Shop ID."),
                                    "name": serializers.CharField(help_text="Shop name."),
                                    "phone": serializers.CharField(help_text="Primary phone number."),
                                    "phone2": serializers.CharField(help_text="Secondary phone number. May be null."),
                                    "address": serializers.CharField(help_text="Shop address. May be null."),
                                    "gst_number": serializers.CharField(help_text="GST number. May be null."),
                                    "image": serializers.CharField(help_text="Shop image URL. May be null."),
                                    "place": serializers.CharField(help_text="Locality or area name. May be null."),
                                    "email": serializers.CharField(help_text="Shop email address. May be null."),
                                    "city": serializers.CharField(help_text="City. May be null."),
                                    "state": serializers.CharField(help_text="State. May be null."),
                                    "pincode": serializers.CharField(help_text="Postal/ZIP code. May be null."),
                                    "shop_role": serializers.CharField(help_text="Purpose: The user's role within this specific shop (e.g. OWNER, MANAGER, STAFF).\nFormat: String. May be null if no shop-user association exists."),
                                    "terms_and_conditions": serializers.ListField(
                                        child=serializers.CharField(),
                                        help_text="Purpose: Shop's terms and conditions.\nFormat: Array of strings. May be null."
                                    ),
                                    "tax_configuration": inline_serializer(
                                        name="ShopTaxConfigurationV5",
                                        fields={
                                            "id": serializers.IntegerField(help_text="Tax configuration row ID."),
                                            "is_enabled": serializers.BooleanField(help_text="Whether GST/tax calculation is active for this shop."),
                                            "tax_name": serializers.CharField(help_text="Display name of the tax (e.g. 'GST')."),
                                            "tax_rate": serializers.FloatField(help_text="Tax rate percentage (e.g. 18.0)."),
                                            "tax_calculation_type": serializers.CharField(help_text="'exclusive' or 'inclusive'."),
                                            "taxable_components": serializers.ListField(
                                                child=serializers.CharField(),
                                                help_text="Which parts of a transaction tax applies to (e.g. product_total, additional_charges, security_amount, entire_invoice)."
                                            ),
                                        },
                                        required=False,
                                        help_text="Purpose: The shop's GST/tax configuration.\nNull if the shop has no tax configuration set up.",
                                    ),
                                },
                            ),
                            "public_access": inline_serializer(
                                name="ShopPublicAccessV5",
                                fields={
                                    "slug": serializers.CharField(
                                        help_text=(
                                            "Purpose: Human-readable URL segment for the shop, derived from the shop name. "
                                            "Format: string (slug, e.g. sharma-rentals)."
                                        )
                                    ),
                                    "public_token": serializers.CharField(
                                        help_text=(
                                            "Purpose: Unique public identifier for the shop. "
                                            "Format: string (10-character nanoid, e.g. X7h29LmQa3)."
                                        )
                                    ),
                                    "public_url": serializers.CharField(
                                        help_text=(
                                            "Purpose: Ready-to-use public shop URL, built from slug and public_token. "
                                            "Format: string (e.g. https://bookiebuddy.in/shop/sharma-rentals/X7h29LmQa3)."
                                        )
                                    ),
                                },
                            ),
                            "organization_public_access": inline_serializer(
                                name="OrganizationPublicAccessV5",
                                fields={
                                    "slug": serializers.CharField(
                                        help_text="Purpose: Public URL slug of the shop's parent organization.\nFormat: String."
                                    ),
                                    "public_token": serializers.CharField(
                                        help_text="Purpose: Unique public identifier for the shop's parent organization.\nFormat: String."
                                    ),
                                    "public_url": serializers.CharField(
                                        help_text=(
                                            "Purpose: Ready-to-use public organization URL, built from slug and public_token. "
                                            "Format: string (e.g. https://bookiebuddy.in/shop/posh-weddings/14tkrYfd6s)."
                                        )
                                    ),
                                },
                                required=False,
                                help_text="Purpose: Public access details for the shop's parent organization (combined multi-shop catalog).\nNull if the shop does not belong to an organization.",
                            ),
                            "ui_feature_access": serializers.ListField(
                                child=inline_serializer(
                                    name="UiFeatureAccessV5",
                                    fields={
                                        "name": serializers.CharField(help_text="Feature name."),
                                        "code": serializers.CharField(help_text="Unique feature code."),
                                        "is_active": serializers.BooleanField(
                                            help_text="Whether the feature is active (always true - only active features are listed)."
                                        ),
                                        "is_shop_level": serializers.BooleanField(
                                            help_text="False for MARKETPLACE (no shop-level access needed); true for every other feature."
                                        ),
                                    },
                                ),
                                help_text=(
                                    "Purpose: Global feature list for UI-level show/hide control in the frontend.\n"
                                    "Format: Array of objects from the global Feature table - active, non-deleted only, "
                                    "not filtered by the active shop."
                                ),
                            ),
                        },
                    ),
                },
            ),
            400: inline_serializer(
                name="UserProfileV5Error400",
                fields={
                    "status": serializers.BooleanField(help_text="Always False on error."),
                    "message": serializers.CharField(help_text="Human-readable error description."),
                    "dev_message": serializers.CharField(help_text="Developer-facing diagnostic message."),
                },
            ),
            404: inline_serializer(
                name="UserProfileV5Error404",
                fields={
                    "status": serializers.BooleanField(help_text="Always False on error."),
                    "message": serializers.CharField(help_text="'User account not found' or 'Shop not found'."),
                    "dev_message": serializers.CharField(help_text="Developer-facing diagnostic message with the missing resource ID."),
                },
            ),
        },
        examples=[
            OpenApiExample(
                name="Success — profile with active subscription",
                response_only=True,
                status_codes=["200"],
                value={
                    "status": True,
                    "message": "User profile retrieved successfully",
                    "dev_message": "User profile data for 42 with shop 7 retrieved",
                    "data": {
                        "id": 42,
                        "phone": "9876543210",
                        "full_name": "Rahul Sharma",
                        "last_name": None,
                        "email": "rahul@example.com",
                        "role": "OWNER",
                        "multiple_shops": False,
                        "has_active_notification": True,
                        "subscription": {
                            "plan": "pro",
                            "status": "active",
                            "features_details": [
                                {"code": "LEDGER", "name": "Ledger", "status": "active", "limit_exceeded": None,
                                 "start_date": None, "end_date": None},
                                {"code": "WHATSAPP_MESSAGE", "name": "WhatsApp Message", "status": "active", "limit_exceeded": False,
                                 "start_date": "19-12-2025 10:15:00", "end_date": "19-12-2026 23:59:32"},
                            ],
                            "expiry_date": "31-12-2025",
                            "days_remaining": 180
                        },
                        "password_settings": [
                            {"setting_name": "LEDGER_VIEW", "requires_password_for": "ALL", "description": "Ledger - View"},
                            {"setting_name": "BOOKING_EDIT", "requires_password_for": "NONE", "description": "Booking - Edit"},
                        ],
                        "shop_settings": {
                            "cooling_days": 1,
                            "default_action": "BOOKINGS",
                            "cooling_period_type": "after",
                            "printer_preference": "ASK_EVERY_TIME"
                        },
                        "shop": {
                            "id": 7,
                            "name": "Sharma Rentals",
                            "phone": "9876543210",
                            "phone2": None,
                            "address": "12 MG Road, Bengaluru",
                            "gst_number": "29ABCDE1234F1Z5",
                            "image": "https://s3.example.com/shops/7/logo.png",
                            "place": "MG Road",
                            "email": "sharma@example.com",
                            "city": "Bengaluru",
                            "state": "Karnataka",
                            "pincode": "560001",
                            "shop_role": "owner",
                            "terms_and_conditions": ["All rentals are subject to availability."],
                            "tax_configuration": {
                                "id": 3,
                                "is_enabled": True,
                                "tax_name": "GST",
                                "tax_rate": 18.0,
                                "tax_calculation_type": "exclusive",
                                "taxable_components": ["product_total", "additional_charges"],
                            },
                        },
                        "public_access": {
                            "slug": "sharma-rentals",
                            "public_token": "X7h29LmQa3",
                            "public_url": "https://bookiebuddy.in/shop/sharma-rentals/X7h29LmQa3",
                        },
                        "organization_public_access": None,
                        "ui_feature_access": [
                            {"name": "Analytics Dashboard", "code": "ANALYTICS", "is_active": True, "is_shop_level": True},
                            {"name": "Marketplace", "code": "MARKETPLACE", "is_active": True, "is_shop_level": False},
                            {"name": "Sales", "code": "SALES", "is_active": True, "is_shop_level": True},
                        ],
                    }
                },
            ),
            OpenApiExample(
                name="Error — user not found",
                response_only=True,
                status_codes=["404"],
                value={
                    "status": False,
                    "message": "User account not found",
                    "dev_message": "User with ID 99 not found in database"
                },
            ),
        ],
    )
    def get(self, request):
        try:
            user = User.objects.prefetch_related("device_tokens").get(id=request.user.id)
        except User.DoesNotExist:
            return self.not_found_response(
                resource="user",
                message="User account not found",
                dev_message=f"User with ID {request.user.id} not found in database"
            )

        # Get active shop id. A single select_related covers subscription/plan,
        # shop settings, organization, and tax configuration - all FK/OneToOne
        # relations, so no extra queries are incurred by the new V5 sections.
        try:
            shop_id = get_active_shop_id(request)
            shop = Shop.objects.select_related(
                "subscription__plan",
                "settings",
                "organization",
                "tax_configuration",
            ).prefetch_related(
                "subscription__plan__included_features",
            ).get(id=shop_id)
        except Shop.DoesNotExist:
            return self.not_found_response(
                message="Shop not found",
                dev_message=f"Shop with ID {shop_id} not found",
                resource="shop"
            )
        except Exception as e:
            return self.error_response(
                message="Error retrieving shop information",
                dev_message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST
            )

        associated_shops_count = ShopUser.objects.filter(user=user).count()
        has_multiple_shops = associated_shops_count > 1

        # Serialize user
        user_data = UserProfileSerializer(user).data

        shop_user = ShopUser.objects.select_related("subscription_access").filter(user=user, shop=shop).first()
        shop_role = shop_user.role if shop_user else None

        has_active_fcm_token = any(
            token.is_active and token.shop_id == shop.id
            for token in user.device_tokens.all()
        )

        # Get subscription information
        subscription_data = {
            "plan": "none",
            "status": shop.subscription_status.lower(),
        }

        try:
            subscription = getattr(shop, "subscription", None)
            if subscription:
                plan = getattr(subscription, "plan", None)
                subscription_data["plan"] = getattr(plan, "plan_type", "none").lower()
                now = timezone.localtime(timezone.now())
                subscription_data["status"] = subscription.get_runtime_status(as_of=now).lower()

                # Collect feature details (V5 exposes only features_details, not a
                # separate plain `features` code list — that was redundant).
                features_details = []

                # Add-on rows (ShopFeatureSubscription) - also the source of every
                # entry's start_date / end_date.
                addon_features = list(ShopFeatureSubscription.objects.filter(
                    shop_subscription=subscription,
                    is_active=True
                ).select_related("feature"))
                addon_by_feature = {}
                for addon in addon_features:
                    addon_by_feature.setdefault(addon.feature_id, addon)

                # 1. Base plan features (dates only when the feature also has a
                # ShopFeatureSubscription row; null otherwise)
                if plan and hasattr(plan, "included_features"):
                    sub_end_date = getattr(subscription, "end_date", None)
                    sub_end_date_local = timezone.localtime(sub_end_date) if sub_end_date else None
                    base_status = "expired" if (sub_end_date_local and sub_end_date_local <= now) else "active"
                    for f in plan.included_features.all():
                        addon = addon_by_feature.get(f.id)
                        features_details.append({
                            "_feature_id": f.id,
                            "code": f.code,
                            "name": f.name,
                            "status": base_status,
                            "start_date": self._format_feature_date(addon.start_date) if addon else None,
                            "end_date": self._format_feature_date(addon.end_date) if addon else None,
                        })

                # 2. Add-on features
                for addon in addon_features:
                    if addon.feature:
                        addon_status = "expired" if timezone.localtime(addon.end_date) <= now else "active"
                        features_details.append({
                            "_feature_id": addon.feature.id,
                            "code": addon.feature.code,
                            "name": addon.feature.name,
                            "status": addon_status,
                            "start_date": self._format_feature_date(addon.start_date),
                            "end_date": self._format_feature_date(addon.end_date),
                        })

                # Attach limit_exceeded (true/false for usage-metered features,
                # null for everything else) - one shared helper, 3 queries total
                # regardless of how many features are in this list. Runs even
                # when there's no plan (addon-only subscriptions) so every entry
                # still gets the key - FeatureUsagePolicy has no rows for a null
                # plan_id, so the helper naturally returns {} and every entry
                # ends up null.
                feature_ids = [entry["_feature_id"] for entry in features_details]
                limit_status = get_feature_limit_status(shop.id, getattr(plan, "id", None), feature_ids)
                for entry in features_details:
                    entry["limit_exceeded"] = limit_status.get(entry.pop("_feature_id"))

                subscription_data["features_details"] = features_details

                # Add end date & days remaining
                if getattr(subscription, "end_date", None):
                    sub_end_local = timezone.localtime(subscription.end_date)
                    subscription_data["expiry_date"] = sub_end_local.strftime("%d-%m-%Y")
                    if sub_end_local.date() > now.date():
                        days_remaining = (sub_end_local.date() - now.date()).days
                        subscription_data["days_remaining"] = days_remaining

        except Exception as e:
            logger.error(f"Error processing subscription for shop {shop.id}: {str(e)}")
            return self.error_response(
                message="Failed to Fetch User data",
                dev_message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST
            )

        # Password settings — list of descriptive objects instead of V4's flat key-value map.
        password_policy_qs = ShopFeaturePolicy.objects.filter(shop=shop, is_active=True)
        password_policy_map = {policy.feature: policy.require_password_for for policy in password_policy_qs}

        # ANALYTICS app: no subscriptions here, so open every feature
        if getattr(settings, "ANALYTICS_MODE", False):
            subscription_data = {
                "plan": "pro",
                "status": "active",
                "features_details": [
                    {"code": f.code, "name": f.name, "status": "active", "limit_exceeded": None,
                     "start_date": None, "end_date": None}
                    for f in Feature.objects.all()
                ],
                "expiry_date": "31-12-2099",
                "days_remaining": 9999,
            }

        feature_descriptions = dict(FeatureKey.choices)
        password_settings = []
        for feature in FeatureKey.values:
            requires_password_for = password_policy_map.get(
                feature,
                self.DEFAULT_PASSWORD_SETTINGS.get(feature, PasswordRequirement.NONE)
            )
            password_settings.append({
                "setting_name": feature,
                "requires_password_for": requires_password_for,
                "description": feature_descriptions.get(feature, feature),
            })

        # Shop settings
        shop_settings = getattr(shop, "settings", None)
        if not shop_settings:
            shop_settings_data = self.DEFAULT_SHOP_SETTINGS
        else:
            shop_settings_data = {
                "cooling_days": shop_settings.cooling_days,
                "default_action": shop_settings.default_action,
                "cooling_period_type": shop_settings.cooling_period_type,
                "printer_preference": shop_settings.printer_preference,
            }

        # Organization public access — null if the shop has no parent organization.
        public_url_base = settings.PUBLIC_SHOP_URL_BASE.rstrip("/")
        organization = getattr(shop, "organization", None)
        organization_public_access = None
        if organization:
            organization_public_access = {
                "slug": organization.slug or "",
                "public_token": organization.public_token or "",
                "public_url": (
                    f"{public_url_base}/{organization.slug}/{organization.public_token}"
                    if organization.slug and organization.public_token else ""
                ),
            }

        # Tax configuration — null if the shop has no tax configuration row.
        tax_config = getattr(shop, "tax_configuration", None)
        tax_configuration_data = None
        if tax_config:
            tax_configuration_data = {
                "id": tax_config.id,
                "is_enabled": tax_config.is_enabled,
                "tax_name": tax_config.tax_name,
                "tax_rate": float(tax_config.tax_rate or 0),
                "tax_calculation_type": tax_config.tax_calculation_type,
                "taxable_components": tax_config.taxable_components or [],
            }

        # Global feature list for frontend show/hide - not tied to the active shop.
        # Default manager already excludes soft-deleted rows.
        ui_feature_access = [
            {
                "name": f["name"],
                "code": f["code"],
                "is_active": f["is_active"],
                "is_shop_level": (f["code"] or "").upper() not in self.NON_SHOP_LEVEL_FEATURE_CODES,
            }
            for f in Feature.objects.order_by("name").values("name", "code", "is_active")
        ]

        # Build the de-duplicated V5 response — shop details live only inside `shop`.
        user_data.update({
            "multiple_shops": has_multiple_shops,
            "has_active_notification": has_active_fcm_token,
            "subscription": subscription_data,
            "password_settings": password_settings,
            "shop_settings": shop_settings_data,
            "shop": {
                "id": shop.id,
                "name": shop.name,
                "phone": shop.phone,
                "phone2": shop.phone2,
                "address": shop.address,
                "gst_number": shop.gst_number,
                "image": shop.img,
                "place": shop.place,
                "email": shop.email,
                "city": shop.city,
                "state": shop.state,
                "pincode": shop.pincode,
                "shop_role": shop_role,
                "terms_and_conditions": shop.terms_and_conditions,
                "tax_configuration": tax_configuration_data,
            },
            "public_access": {
                "slug": shop.slug or "",
                "public_token": shop.public_token or "",
                "public_url": (
                    f"{public_url_base}/{shop.slug}/{shop.public_token}"
                    if shop.slug and shop.public_token else ""
                ),
            },
            "organization_public_access": organization_public_access,
            "ui_feature_access": ui_feature_access,
        })

        return self.success_response(
            data=user_data,
            message="User profile retrieved successfully",
            dev_message=f"User profile data for {user.id} with shop {shop.id} retrieved"
        )
