from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import Feature
from subscriptions.api.admin_v3.serializers import FeatureSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Retrieve feature",
    description="Returns one feature by id, including related usage policies through serializer.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Feature id.")],
    responses={
        200: OpenApiResponse(description="Feature object response."),
        400: OpenApiResponse(description="Exact messages: Feature ID is required. / Invalid feature ID format."),
        404: OpenApiResponse(description="Exact message: Feature not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while retrieving the feature."),
    },
    examples=[OpenApiExample("Success response", value={"id": 4, "name": "Advanced Reports", "code": "ADV_REPORTS", "usage_type": "COUNT"}, response_only=True, status_codes=["200"])],
)
def retrieve(self, request, pk=None):
    """
    Retrieve a specific feature by ID
    """
    try:
        if not pk:
            return Response(
                {"error": "Feature ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Prefetch usage policies for optimization
            feature = Feature.objects.prefetch_related(
                'featureusagepolicy_set__plan'
            ).get(pk=pk)
        except Feature.DoesNotExist:
            return Response(
                {"error": "Feature not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = FeatureSerializer(feature)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except ValueError:
        return Response(
            {"error": "Invalid feature ID format."},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception("Error in FeatureViewSet.retrieve")
        return Response(
            {"error": "An error occurred while retrieving the feature."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
