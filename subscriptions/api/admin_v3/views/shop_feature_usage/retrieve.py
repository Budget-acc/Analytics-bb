import logging
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import ShopFeatureUsage
from .list import _serialize

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="Retrieve shop feature usage",
    description="Returns a single feature usage record by id.",
    parameters=[
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="ShopFeatureUsage id."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Feature usage retrieved successfully."),
        404: OpenApiResponse(description="Exact message: Record not found."),
        500: OpenApiResponse(description="Exact message: Could not retrieve record."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Feature usage retrieved successfully.",
                "data": {
                    "id": 1, "shop": 3, "shop_name": "ABC Boutique",
                    "feature": 4, "feature_code": "WHATSAPP_MSG", "feature_name": "WhatsApp Messaging",
                    "used": 12, "period_start": "2026-06-01T00:00:00Z", "period_end": "2026-06-30T23:59:59Z",
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def retrieve(self, request, pk=None):
    try:
        obj = (
            ShopFeatureUsage.objects
            .select_related("shop", "feature")
            .get(pk=pk)
        )

        return self.success_response(
            data=_serialize(obj),
            message="Feature usage retrieved successfully.",
            dev_message=f"ShopFeatureUsage #{pk}",
        )

    except ShopFeatureUsage.DoesNotExist:
        return self.not_found_response("ShopFeatureUsage", message="Record not found.")
    except Exception as e:
        logger.error(f"ShopFeatureUsageViewSet.retrieve:\n{e}", exc_info=True)
        return self.error_response(
            message="Could not retrieve record.",
            dev_message=str(e),
        )
