from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import SubscriptionPlan
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Delete subscription plan",
    description="Soft deletes a subscription plan if it is not used by active subscriptions.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Subscription plan id.")],
    responses={
        200: OpenApiResponse(description="Success delete response."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Plan ID is required.\n"
                "- Cannot delete plan. It is currently being used by {n} active subscription(s).\n"
                "- Invalid plan ID format."
            )
        ),
        404: OpenApiResponse(description="Exact message: Subscription plan not found."),
        500: OpenApiResponse(description="Exact message: An error occurred while deleting the subscription plan."),
    },
    examples=[
        OpenApiExample("Success response", value={"message": "Subscription plan 'Pro' deleted successfully."}, response_only=True, status_codes=["200"]),
    ],
)
def destroy(self, request, pk=None):
    """
    Soft delete a subscription plan
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

        # Check if plan is being used by any active subscriptions
        if hasattr(plan, 'shopsubscription_set'):
            active_subscriptions = plan.shopsubscription_set.filter(status="ACTIVE").count()
            if active_subscriptions > 0:
                return Response(
                    {"error": f"Cannot delete plan. It is currently being used by {active_subscriptions} active subscription(s)."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        plan_name = plan.name
        plan.delete()  # Soft delete

        return Response(
            {"message": f"Subscription plan '{plan_name}' deleted successfully."},
            status=status.HTTP_200_OK
        )

    except ValueError:
        return Response(
            {"error": "Invalid plan ID format."},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.exception("Error in SubscriptionPlanViewSet.destroy")
        return Response(
            {"error": "An error occurred while deleting the subscription plan."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
