from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db import transaction
from subscriptions.models import ShopSubscription, ShopFeatureSubscription
from safedelete.models import HARD_DELETE
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Remove subscribed feature",
    description="Removes one feature from a shop subscription.",
    parameters=[
        OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop subscription id."),
        OpenApiParameter("feature_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Feature id linked to subscription."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Feature removed from subscription successfully."),
        400: OpenApiResponse(description="Exact messages: Subscription ID is required. / Feature ID is required."),
        404: OpenApiResponse(description="Exact messages: Subscription not found. / Feature not found on this subscription."),
        500: OpenApiResponse(description="Exact message: Unable to remove feature right now. Please try again later."),
    },
    examples=[
        OpenApiExample("Success response", value={"message": "Feature removed from subscription successfully.", "data": {"feature_id": 17, "feature_name": "Premium Reports"}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=True, methods=['delete'], url_path=r'features/(?P<feature_id>[^/.]+)')
@transaction.atomic
def delete_feature(self, request, pk=None, feature_id=None):
    """
    DELETE /api/v3/subscriptions/<subscription_id>/features/<feature_id>/

    Removes (soft-deletes) the ShopFeatureSubscription row linking the feature to subscription.

    Sample Request:
    DELETE /api/v3/subscriptions/5/features/17/

    Sample Response (200):
    {
        "message": "Feature removed from subscription successfully.",
        "data": {
            "feature_id": 17,
            "feature_name": "Premium Reports"
        }
    }
    """
    try:
        # Validate pk
        if not pk:
            return Response(
                {"message": "Subscription ID is required.", "dev_message": "pk parameter missing"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            subscription = ShopSubscription.objects.select_for_update().get(pk=int(pk))
        except (ShopSubscription.DoesNotExist, ValueError):
            return Response(
                {"message": "Subscription not found.", "dev_message": f"ShopSubscription id={pk} not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validate feature_id
        if not feature_id:
            return Response(
                {"message": "Feature ID is required.", "dev_message": "feature_id parameter missing"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            sfs = ShopFeatureSubscription.objects.select_for_update().select_related("feature").get(
                shop_subscription=subscription,
                feature_id=int(feature_id)
            )
        except (ShopFeatureSubscription.DoesNotExist, ValueError):
            return Response(
                {"message": "Feature not found on this subscription.", "dev_message": f"feature_id={feature_id} not linked to subscription {pk}"},
                status=status.HTTP_404_NOT_FOUND
            )

        feature_info = {
            "feature_id": sfs.feature.id,
            "feature_name": sfs.feature.name
        }

        # Soft-delete via safedelete
        sfs.delete(force_policy=HARD_DELETE)

        return Response(
            {
                "message": "Feature removed from subscription successfully.",
                "data": feature_info
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        logger.error(f"Failed deleting feature {feature_id} from subscription {pk}: {e}")
        return Response(
            {
                "message": "Unable to remove feature right now. Please try again later.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
