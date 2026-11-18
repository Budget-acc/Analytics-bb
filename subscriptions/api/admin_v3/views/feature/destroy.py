from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import Feature
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Delete feature",
    description="Soft deletes a feature if it is not actively used and no active feature depends on it.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Feature id.")],
    responses={
        200: OpenApiResponse(description="Success delete response."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Feature ID is required.\n"
                "- Cannot delete feature. It is currently being used by {n} active subscription(s) or plan(s).\n"
                "- Cannot delete feature. The following features depend on it: {feature names}\n"
                "- Invalid feature ID format."
            )
        ),
        404: OpenApiResponse(description="Exact message: Feature not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while deleting the feature."),
    },
    examples=[OpenApiExample("Success response", value={"message": "Feature 'Advanced Reports' deleted successfully."}, response_only=True, status_codes=["200"])],
)
def destroy(self, request, pk=None):
    """
    Soft delete a feature
    """
    try:
        if not pk:
            return Response(
                {"error": "Feature ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            feature = Feature.objects.get(pk=pk)
        except Feature.DoesNotExist:
            return Response(
                {"error": "Feature not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if feature is being used by any active subscriptions
        active_usage_count = 0

        # Check subscription plans
        if hasattr(feature, 'base_plans'):
            active_usage_count += feature.base_plans.filter(is_active=True).count()

        # Check shop feature subscriptions
        if hasattr(feature, 'shop_subscriptions'):
            active_usage_count += feature.shop_subscriptions.filter(
                shopfeaturesubscription__is_active=True
            ).count()

        # Check bundle subscriptions
        if hasattr(feature, 'shopbundlesubscription_set'):
            active_usage_count += feature.shopbundlesubscription_set.filter(
                is_active=True
            ).count()

        if active_usage_count > 0:
            return Response(
                {"error": f"Cannot delete feature. It is currently being used by {active_usage_count} active subscription(s) or plan(s)."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if other features depend on this feature
        dependent_features = Feature.objects.filter(requires_features=feature, is_active=True)
        if dependent_features.exists():
            dependent_names = [f.name for f in dependent_features[:3]]
            more_count = dependent_features.count() - 3
            dependent_str = ", ".join(dependent_names)
            if more_count > 0:
                dependent_str += f" and {more_count} more"

            return Response(
                {"error": f"Cannot delete feature. The following features depend on it: {dependent_str}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        feature_name = feature.name
        feature.delete()  # Soft delete

        return Response(
            {"message": f"Feature '{feature_name}' deleted successfully."},
            status=status.HTTP_200_OK
        )

    except ValueError:
        return Response(
            {"error": "Invalid feature ID format."},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception("Error in FeatureViewSet.destroy")
        return Response(
            {"error": "An error occurred while deleting the feature."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
