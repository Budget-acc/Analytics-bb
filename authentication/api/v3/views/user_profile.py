from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from authentication.models import User
from authentication.api.v3.serializers import UserProfileSerializer
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id, get_active_shop_and_user
from shop.models import Shop, ShopUser, ShopFeaturePolicy, FeatureKey, PasswordRequirement
from subscriptions.models import ShopFeatureSubscription, ShopUserFeature, UserFeature
from django.utils import timezone
import logging
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger(__name__)


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
    }

    DEFAULT_SHOP_SETTINGS = {
        "cooling_days": 0,
        "default_action": "BOOKINGS",
        "search_client": False,
        "cooling_period_type": "after",
    }

    @extend_schema(
        tags=["User Management V3"],
        summary="Profile details",
        description=(
            "Returns authenticated user profile details, active shop information, subscription/access metadata, "
            "password settings, and shop settings.\n\n"
            "Purpose: Single payload for user profile/dashboard initialization in v3 clients.\n\n"
            "Context: Requires valid JWT and active shop context from request headers/session selection."
        ),
        responses={
            200: inline_serializer(
                name="UserProfileSuccessV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "dev_message": serializers.CharField(),
                    "data": serializers.DictField(help_text="User profile payload containing user, shop, subscription, permissions, and settings details."),
                },
            ),
            400: inline_serializer(
                name="UserProfileBadRequestV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Error summary for invalid/failed shop context resolution."),
                    "dev_message": serializers.CharField(help_text="Developer-oriented error details."),
                },
            ),
            404: inline_serializer(
                name="UserProfileNotFoundV3",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Resource not found message for user or shop."),
                    "dev_message": serializers.CharField(help_text="Developer detail of missing resource."),
                    "resource": serializers.CharField(help_text="Missing resource identifier, such as user or shop."),
                },
            ),
        },
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
            shop = Shop.objects.get(id=shop_id)
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

        shop_user = ShopUser.objects.filter(user=user, shop=shop).first()
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
                subscription_data["status"] = getattr(subscription, "status", "inactive").lower()

                # Collect features
                features = []

                # 1. Base plan features
                if plan and hasattr(plan, "included_features"):
                    features.extend([f.code for f in plan.included_features.all()])

                # 2. Add-on features
                addon_features = ShopFeatureSubscription.objects.filter(
                    shop_subscription=subscription,
                    is_active=True
                ).select_related("feature")

                for addon in addon_features:
                    if addon.feature:
                        features.append(addon.feature.code)

                # User-specific accessible features
                if hasattr(shop_user, "subscription_access") and shop_user.subscription_access:
                    user_features = shop_user.subscription_access.get_accessible_features()
                    subscription_data["features"] = [f.code for f in user_features if f]
                else:
                    subscription_data["features"] = features

                # Add end date & days remaining
                if getattr(subscription, "end_date", None):
                    subscription_data["expiry_date"] = subscription.end_date.strftime("%d-%m-%Y")
                    if subscription.end_date > timezone.now():
                        days_remaining = (subscription.end_date.date() - timezone.now().date()).days
                        subscription_data["days_remaining"] = days_remaining

        except Exception as e:
            logger.error(f"Error processing subscription for shop {shop.id}: {str(e)}")
            return self.error_response(
                message="Failed to Fetch User data",
                dev_message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST
            )

        # ===== USER FEATURES LOGIC =====
        user_features = []

        try:
            if shop_user:
                role_feature_codes = UserFeature.objects.filter(
                    role=shop_user.role
                ).values_list("feature__code", flat=True)

                features_set = set(role_feature_codes)

                custom_features = ShopUserFeature.objects.filter(
                    shop_user=shop_user
                ).select_related("feature")

                for cf in custom_features:
                    if cf.granted:
                        features_set.add(cf.feature.code)
                    else:
                        features_set.discard(cf.feature.code)

                user_features = list(features_set)

        except Exception as e:
            logger.error(f"Error fetching user features: {e}")
            user_features = []

        subscription_data["user_features"] = user_features


        # Password settings
        password_settings_qs = ShopFeaturePolicy.objects.filter(shop=shop, is_active=True)
        password_settings = {policy.feature: policy.require_password_for for policy in password_settings_qs}

        for feature in FeatureKey.values:
            if feature not in password_settings:
                password_settings[feature] = self.DEFAULT_PASSWORD_SETTINGS.get(
                    feature, PasswordRequirement.NONE
                )
        password_settings.pop("PRODUCT_EDIT", None)

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
        })

        return self.success_response(
            data=user_data,
            message="User profile retrieved successfully",
            dev_message=f"User profile data for {user.id} with shop {shop.id} retrieved"
        )
