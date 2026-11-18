from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from rest_framework.permissions import IsAuthenticated
from ERP.utils.mixins import StandardResponseMixin
from rest_framework.views import APIView
from rest_framework import status
from django.utils import timezone
from authentication.api.v4.serializers import UserProfileSerializer
from authentication.models import User
from shop.api.v3.utils.shop import get_active_shop_id
from shop.models import Shop, ShopUser, FeatureKey, PasswordRequirement, ShopFeaturePolicy
from subscriptions.models import ShopFeatureSubscription
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
        "search_client": False,
        "cooling_period_type": "after",
    }

    @extend_schema(
        tags=["User Management V4"],
        summary="Retrieve authenticated user profile",
        description=(
            "Returns the full profile of the currently authenticated user, including personal "
            "details, the active shop's information, subscription plan and features, "
            "shop-level password policies, and shop settings.\n\n"
            "The active shop is resolved from the JWT token's shop context. "
            "All subscription feature codes, expiry dates, and remaining days are included "
            "when a subscription is active. Password settings default to system defaults when "
            "no shop-level policy is configured."
        ),
        responses={
            200: inline_serializer(
                name="UserProfileV4Response",
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
                        name="UserProfileV4Data",
                        fields={
                            "id": serializers.IntegerField(
                                help_text=(
                                    "Purpose: Unique identifier for the user.\n"
                                    "Format: Integer."
                                )
                            ),
                            "name": serializers.CharField(
                                help_text=(
                                    "Purpose: Display name of the user.\n"
                                    "Format: String."
                                )
                            ),
                            "phone": serializers.CharField(
                                help_text=(
                                    "Purpose: Primary phone number of the user.\n"
                                    "Format: String (E.164 or local format)."
                                )
                            ),
                            "email": serializers.CharField(
                                help_text=(
                                    "Purpose: Email address of the user.\n"
                                    "Format: String. May be null if not set."
                                )
                            ),
                            "shop_id": serializers.IntegerField(
                                help_text=(
                                    "Purpose: ID of the active shop resolved from the JWT context.\n"
                                    "Format: Integer."
                                )
                            ),
                            "shop_name": serializers.CharField(
                                help_text=(
                                    "Purpose: Name of the active shop.\n"
                                    "Format: String."
                                )
                            ),
                            "shop_phone": serializers.CharField(
                                help_text=(
                                    "Purpose: Primary phone number of the active shop.\n"
                                    "Format: String."
                                )
                            ),
                            "shop_address": serializers.CharField(
                                help_text=(
                                    "Purpose: Physical address of the active shop.\n"
                                    "Format: String. May be null."
                                )
                            ),
                            "gst_number": serializers.CharField(
                                help_text=(
                                    "Purpose: GST registration number of the active shop.\n"
                                    "Format: String. May be null."
                                )
                            ),
                            "shop_role": serializers.CharField(
                                help_text=(
                                    "Purpose: The user's role in the active shop (e.g., owner, staff).\n"
                                    "Format: String. May be null if no shop-user association exists."
                                )
                            ),
                            "shop_image": serializers.CharField(
                                help_text=(
                                    "Purpose: URL or path to the shop's logo/image.\n"
                                    "Format: String. May be null."
                                )
                            ),
                            "terms_and_conditions": serializers.CharField(
                                help_text=(
                                    "Purpose: Shop's terms and conditions text.\n"
                                    "Format: String. May be null."
                                )
                            ),
                            "multiple_shops": serializers.BooleanField(
                                help_text=(
                                    "Purpose: Indicates whether the user is associated with more than one shop.\n"
                                    "Format: Boolean."
                                )
                            ),
                            "has_active_notification": serializers.BooleanField(
                                help_text=(
                                    "Purpose: True if the user has an active FCM device token registered for the active shop.\n"
                                    "Format: Boolean."
                                )
                            ),
                            "subscription": inline_serializer(
                                name="SubscriptionV4Info",
                                fields={
                                    "plan": serializers.CharField(
                                        help_text="Subscription plan type (e.g., 'basic', 'pro'). 'none' if no plan."
                                    ),
                                    "status": serializers.CharField(
                                        help_text="Subscription status (e.g., 'active', 'inactive')."
                                    ),
                                    "features": serializers.ListField(
                                        child=serializers.CharField(),
                                        help_text="List of feature codes accessible to the user under the current plan."
                                    ),
                                    "features_details": serializers.ListField(
                                        child=inline_serializer(
                                            name="FeatureDetailV4",
                                            fields={
                                                "code": serializers.CharField(help_text="Feature code identifier."),
                                                "name": serializers.CharField(help_text="Human-readable feature name."),
                                                "status": serializers.CharField(help_text="'active' if valid, 'expired' if end date has passed."),
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
                            "password_settings": serializers.DictField(
                                child=serializers.CharField(),
                                help_text=(
                                    "Purpose: Map of feature keys to their password requirement level (e.g., 'ALL', 'STAFF', 'NONE').\n"
                                    "Format: Object with string keys and string values."
                                ),
                            ),
                            "shop_settings": inline_serializer(
                                name="ShopSettingsV4",
                                fields={
                                    "cooling_days": serializers.IntegerField(
                                        help_text="Number of cooling days applied after a booking."
                                    ),
                                    "default_action": serializers.CharField(
                                        help_text="Default landing action for the shop (e.g., 'BOOKINGS')."
                                    ),
                                    "search_client": serializers.BooleanField(
                                        help_text="Whether client search is enabled for the shop."
                                    ),
                                    "cooling_period_type": serializers.CharField(
                                        help_text="Type of cooling period calculation ('after' or other variants)."
                                    ),
                                },
                            ),
                            "shop": inline_serializer(
                                name="ShopDetailV4",
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
                                    "terms_and_conditions": serializers.CharField(help_text="Terms and conditions text. May be null."),
                                },
                            ),
                            "public_access": inline_serializer(
                                name="ShopPublicAccessV4",
                                fields={
                                    "slug": serializers.CharField(
                                        help_text=(
                                            "Purpose: Human-readable URL segment for the shop, derived from the shop name. "
                                            "Used alongside public_token to build the public QR code URL. "
                                            "Format: string (slug, e.g. sharma-rentals). "
                                            "Never null after profile fetch — generated on first access if missing."
                                        )
                                    ),
                                    "public_token": serializers.CharField(
                                        help_text=(
                                            "Purpose: Unique public identifier for the shop. "
                                            "Used in public API paths and QR code URLs to identify the shop "
                                            "without exposing the internal database ID. "
                                            "Format: string (10-character nanoid, e.g. X7h29LmQa3). "
                                            "Never null after profile fetch — generated on first access if missing."
                                        )
                                    ),
                                },
                            ),
                        },
                    ),
                },
            ),
            400: inline_serializer(
                name="UserProfileV4Error400",
                fields={
                    "status": serializers.BooleanField(help_text="Always False on error."),
                    "message": serializers.CharField(help_text="Human-readable error description."),
                    "dev_message": serializers.CharField(help_text="Developer-facing diagnostic message."),
                },
            ),
            404: inline_serializer(
                name="UserProfileV4Error404",
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
                        "name": "Rahul Sharma",
                        "phone": "9876543210",
                        "email": "rahul@example.com",
                        "shop_id": 7,
                        "shop_name": "Sharma Rentals",
                        "shop_phone": "9876543210",
                        "shop_address": "12 MG Road, Bengaluru",
                        "gst_number": "29ABCDE1234F1Z5",
                        "shop_role": "owner",
                        "shop_image": "https://s3.example.com/shops/7/logo.png",
                        "terms_and_conditions": "All rentals are subject to availability.",
                        "multiple_shops": False,
                        "has_active_notification": True,
                        "subscription": {
                            "plan": "pro",
                            "status": "active",
                            "features": ["LEDGER", "REPORTS", "WHATSAPP"],
                            "expiry_date": "31-12-2025",
                            "days_remaining": 180
                        },
                        "password_settings": {
                            "BOOKING_DELETE": "ALL",
                            "LEDGER_VIEW": "ALL",
                            "MONTHLY_GROSS_VIEW": "ALL",
                            "BOOKING_EDIT": "NONE",
                            "BOOKING_PAYMENT": "NONE",
                            "TRANSFER_PRODUCT": "ALL",
                            "PRODUCT_DELETION": "ALL",
                            "PRODUCT_EDIT": "STAFF",
                            "SHOP_PERFORMANCE": "ALL"
                        },
                        "shop_settings": {
                            "cooling_days": 1,
                            "default_action": "BOOKINGS",
                            "search_client": False,
                            "cooling_period_type": "after"
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
                            "terms_and_conditions": "All rentals are subject to availability."
                        }
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
            user = User.objects.prefetch_related("device_tokens") .get(id=request.user.id)

        except User.DoesNotExist:
            return self.not_found_response(
                resource="user",
                message="User account not found",
                dev_message=f"User with ID {request.user.id} not found in database"
            )
        # Get active shop id
        try:
            shop_id = get_active_shop_id(request)
            shop = Shop.objects.select_related(
                "subscription__plan",
                "settings",
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

        shop_slug = shop.slug or ""
        shop_public_token = shop.public_token or ""

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
            "features": []
        }

        try:
            subscription = getattr(shop, "subscription", None)
            if subscription:
                plan = getattr(subscription, "plan", None)
                subscription_data["plan"] = getattr(plan, "plan_type", "none").lower()
                now = timezone.localtime(timezone.now())
                subscription_data["status"] = subscription.get_runtime_status(as_of=now).lower()

                # Collect features
                features = []
                features_details = []

                # 1. Base plan features — fetched once, reused for both features and features_details
                # Expired base plan features are intentionally kept in `features` for backward
                # compatibility with the production Flutter client; `features_details` carries status.
                if plan and hasattr(plan, "included_features"):
                    sub_end_date = getattr(subscription, "end_date", None)
                    sub_end_date_local = timezone.localtime(sub_end_date) if sub_end_date else None
                    base_status = "expired" if (sub_end_date_local and sub_end_date_local <= now) else "active"
                    for f in plan.included_features.all():
                        features.append(f.code)
                        features_details.append({"code": f.code, "name": f.name, "status": base_status})

                # 2. Add-on features
                addon_features = ShopFeatureSubscription.objects.filter(
                    shop_subscription=subscription,
                    is_active=True
                ).select_related("feature")

                for addon in addon_features:
                    if addon.feature:
                        features.append(addon.feature.code)
                        addon_status = "expired" if timezone.localtime(addon.end_date) <= now else "active"
                        features_details.append({
                            "code": addon.feature.code,
                            "name": addon.feature.name,
                            "status": addon_status,
                        })

                # User-specific accessible features
                if hasattr(shop_user, "subscription_access") and shop_user.subscription_access:
                    user_features = shop_user.subscription_access.get_accessible_features()
                    subscription_data["features"] = [f.code for f in user_features if f]
                else:
                    subscription_data["features"] = features

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

        # Password settings
        password_settings_qs = ShopFeaturePolicy.objects.filter(shop=shop, is_active=True)
        password_settings = {policy.feature: policy.require_password_for for policy in password_settings_qs}

        for feature in FeatureKey.values:
            if feature not in password_settings:
                password_settings[feature] = self.DEFAULT_PASSWORD_SETTINGS.get(
                    feature, PasswordRequirement.NONE
                )

        # Shop settings
        shop_settings = getattr(shop, "settings", None)
        if not shop_settings:
            shop_settings_data = self.DEFAULT_SHOP_SETTINGS
        else:
            shop_settings_data = {
                "cooling_days": shop_settings.cooling_days,
                "default_action": shop_settings.default_action,
                "search_client": shop_settings.search_client,
                "cooling_period_type": shop_settings.cooling_period_type,
            }

        # Merge shop details into user_data
        user_data.update({
            "shop_id": shop.id,
            "shop_name": shop.name,
            "shop_phone": shop.phone,
            "shop_address": shop.address,
            "gst_number": shop.gst_number,
            "shop_role": shop_role,
            "shop_image": shop.img,
            "terms_and_conditions": shop.terms_and_conditions,
            "multiple_shops":has_multiple_shops,
            "subscription": subscription_data,
            "password_settings": password_settings,
            "shop_settings": shop_settings_data,
            "has_active_notification": has_active_fcm_token,
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
                "terms_and_conditions": shop.terms_and_conditions,
            },
            "public_access": {
                "slug": shop_slug,
                "public_token": shop_public_token,
            },
        })

        return self.success_response(
            data=user_data,
            message="User profile retrieved successfully",
            dev_message=f"User profile data for {user.id} with shop {shop.id} retrieved"
        )
