from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from subscriptions.models import ShopUserFeature
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Update shop-user feature access",
    description="Updates granted flag for an existing shop-user feature access record.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="ShopUserFeature id.")],
    request=inline_serializer(
        name="SubscriptionsAdminV3PatchShopUserFeatureRequest",
        fields={
            "granted": serializers.BooleanField(required=True),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Access updated."),
        400: OpenApiResponse(description="Exact messages: ID required for update. / Granted flag required."),
        404: OpenApiResponse(description="Not found response for ShopUserFeature."),
        500: OpenApiResponse(description="Exact message: Could not update access."),
    },
    examples=[
        OpenApiExample("Request example", value={"granted": False}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Access updated.", "data": {"id": 14, "user": 25, "feature": 4, "granted": False}}, response_only=True, status_codes=["200"]),
    ],
)
@transaction.atomic
def partial_update(self, request, pk=None):
    """
    Accept same format as create:
    {
      "user_id": 25,
      "feature": 4,
      "granted": false
    }
    """
    try:
        if not pk:
            return self.validation_error_response(
                message="ID required for update."
            )

        obj = ShopUserFeature.objects.select_related("shop_user", "feature").get(pk=pk)

        data = request.data
        granted = data.get("granted")

        if granted is None:
            return self.validation_error_response(
                message="Granted flag required.",
                errors={"granted": ["This field is required"]}
            )

        obj.granted = bool(granted)
        obj.save(update_fields=["granted"])

        return self.success_response(
            message="Access updated.",
            dev_message=f"ShopUserFeature #{obj.id}",
            data={
                "id": obj.id,
                "user": obj.shop_user.user_id,
                "shop_user": obj.shop_user_id,
                "feature": obj.feature_id,
                "feature_code": obj.feature.code,
                "feature_name": obj.feature.name,
                "granted": obj.granted,
            }
        )

    except ShopUserFeature.DoesNotExist:
        return self.not_found_response("ShopUserFeature")
    except Exception as e:
        logger.error(f"ShopUserFeatureViewSet.update:\n,{e}")
        return self.error_response(
            message="Could not update access.",
            dev_message=str(e),
        )
