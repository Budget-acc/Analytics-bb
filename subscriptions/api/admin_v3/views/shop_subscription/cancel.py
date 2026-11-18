from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.shop_subscription_service import ShopSubscriptionService
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Cancel subscription",
    description="Cancels a shop subscription by id.",
    parameters=[OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop subscription id.")],
    responses={
        200: OpenApiResponse(description="Exact status message: Subscription cancelled successfully"),
        400: OpenApiResponse(description="Returns error from service exception using exact error field."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "Subscription cancelled successfully"}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=['post'], url_path='cancel/(?P<pk>[^/.]+)')
def cancel(self, request, pk=None):
    """Cancel a subscription"""
    shop_subscription = self.get_object()

    try:
        ShopSubscriptionService.cancel_shop_subscription(shop_subscription)
        return Response(
            {'status': 'Subscription cancelled successfully'},
            status=status.HTTP_200_OK
        )
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )
