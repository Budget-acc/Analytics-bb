from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import SubscriptionPlan
from subscriptions.api.admin_v3.serializers import SubscriptionPlanSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Retrieve subscription plan",
    description="Returns one subscription plan by id.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Subscription plan id.")],
    responses={
        200: OpenApiResponse(description="Subscription plan object."),
        400: OpenApiResponse(description="Exact messages: Plan ID is required. / Invalid plan ID format."),
        404: OpenApiResponse(description="Exact message: Subscription plan not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while retrieving the subscription plan."),
    },
    examples=[
        OpenApiExample("Success response", value={"id": 2, "name": "Pro", "plan_type": "MONTHLY", "base_price": "999.00"}, response_only=True, status_codes=["200"]),
    ],
)
def retrieve(self, request, pk=None):
    """
    Retrieve a specific subscription plan by ID
    """
    try:
        if not pk:
            return Response(
                {"error": "Plan ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            plan = SubscriptionPlan.objects.get(pk=pk)
        except SubscriptionPlan.DoesNotExist:
            return Response(
                {"error": "Subscription plan not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = SubscriptionPlanSerializer(plan)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except ValueError:
        return Response(
            {"error": "Invalid plan ID format."},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception("Error in SubscriptionPlanViewSet.retrieve")
        return Response(
            {"error": "An error occurred while retrieving the subscription plan."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
