from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from subscriptions.models import ShopUserFeature
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Create shop-user feature access",
    description="Creates a grant/block access record for a feature and shop user.",
    request=inline_serializer(
        name="SubscriptionsAdminV3CreateShopUserFeatureRequest",
        fields={
            "user_id": serializers.IntegerField(required=True),
            "shop_id": serializers.IntegerField(required=False),
            "feature": serializers.IntegerField(required=True),
            "granted": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response with exact messages: Access granted. or Access blocked."),
        400: OpenApiResponse(
            description=(
                "Error messages include:\n"
                "- User ID is required.\n"
                "- Shop user not found.\n"
                "- Internal lookup failed.\n"
                "- Feature ID is required.\n"
                "- Feature does not exist or inactive.\n"
                "- Internal feature lookup failed.\n"
                "- Access entry already exists.\n"
                "- Duplicate access entry exists.\n"
                "- Could not create access."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"user_id": 25, "shop_id": 5, "feature": 4, "granted": True}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Access granted.", "data": {"id": 14, "shop_user": 81, "user": 25, "feature": 4, "granted": True}}, response_only=True, status_codes=["200"]),
    ],
)
@transaction.atomic
def create(self, request):
    """
    Request format:
    {
    "user_id": 25,
    "shop_id": 5,       # optional if you want deterministic
    "feature": 4,
    "granted": true
    }
    """
    try:
        data = request.data
        user_id = data.get("user_id")
        shop_id = data.get("shop_id")
        feature_id = data.get("feature")
        granted = bool(data.get("granted", True))

        # 1. Validate user
        if not user_id:
            return self.validation_error_response(
                message="User ID is required.",
                errors={"user_id": ["This field is required."]}
            )

        shop_user, err = self._resolve_shop_user(user_id, shop_id)
        if err:
            return self.validation_error_response(message=err)

        # 2. Validate Feature
        if not feature_id:
            return self.validation_error_response(
                message="Feature ID is required.",
                errors={"feature": ["This field is required."]}
            )

        feature, err = self._get_feature(feature_id)
        if err:
            return self.validation_error_response(message=err)

        # 3. Check if already exists
        if ShopUserFeature.objects.filter(shop_user_id=shop_user.id, feature_id=feature.id).exists():
            return self.error_response(
                message="Access entry already exists.",
                dev_message="Duplicate ShopUserFeature",
            )

        # 4. Create new record
        obj = ShopUserFeature.objects.create(
            shop_user_id=shop_user.id,
            feature_id=feature.id,
            granted=granted
        )

        return self.success_response(
            message="Access granted." if granted else "Access blocked.",
            dev_message="created ShopUserFeature",
            data={
                "id": obj.id,
                "shop_user": obj.shop_user_id,
                "user": obj.shop_user.user_id,
                "feature": obj.feature_id,
                "granted": obj.granted,
            },
        )

    except IntegrityError:
        return self.error_response(
            message="Duplicate access entry exists.",
            dev_message="unique_together collision"
        )
    except Exception as e:
        logger.error(f"ShopUserFeatureViewSet.create:\n,{e}")
        return self.error_response(
            message="Could not create access.",
            dev_message=str(e),
        )
