import logging
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import ShopFeatureUsageResetLog

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="List shop feature usage reset logs",
    description=(
        "Returns a paginated audit log of all administrative resets performed on shop feature usage records. "
        "Filter by usage_id to see the reset history for a specific record, or by shop_id to see all resets "
        "for a given shop. Results are ordered most-recent first."
    ),
    parameters=[
        OpenApiParameter("usage_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Filter by ShopFeatureUsage id."),
        OpenApiParameter("shop_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Filter by shop id."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated reset log list."),
        500: OpenApiResponse(description="Exact message: Unable to fetch reset logs."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "count": 1, "next": None, "previous": None,
                "results": [{
                    "id": 7,
                    "usage": 1,
                    "shop": 3,
                    "shop_name": "ABC Boutique",
                    "feature": 4,
                    "feature_code": "WHATSAPP_MSG",
                    "feature_name": "WhatsApp Messaging",
                    "previous_value": 12,
                    "new_value": 0,
                    "reason": "End of billing period",
                    "reset_by": 5,
                    "reset_by_name": "Admin User",
                    "reset_at": "2026-06-26T10:30:00Z",
                }],
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
@action(detail=False, methods=["get"], url_path="reset-logs")
def list_reset_logs(self, request):
    try:
        usage_id = request.query_params.get("usage_id")
        shop_id = request.query_params.get("shop_id")

        qs = (
            ShopFeatureUsageResetLog.objects
            .select_related("usage__shop", "usage__feature", "reset_by")
            .order_by("-reset_at")
        )

        if usage_id:
            qs = qs.filter(usage_id=usage_id)
        if shop_id:
            qs = qs.filter(usage__shop_id=shop_id)

        paginator = PageNumberPagination()
        items = paginator.paginate_queryset(qs, request)

        data = [_serialize_log(i) for i in items]

        return self.success_response(
            data=paginator.get_paginated_response(data).data
        )

    except Exception as e:
        logger.error(f"ShopFeatureUsageViewSet.list_reset_logs:\n{e}", exc_info=True)
        return self.error_response(
            message="Unable to fetch reset logs.",
            dev_message=str(e),
        )


def _serialize_log(log):
    reset_by = log.reset_by
    if reset_by:
        reset_by_name = reset_by.full_name or str(reset_by.phone)
    else:
        reset_by_name = None

    return {
        "id": log.id,
        "usage": log.usage_id,
        "shop": log.usage.shop_id,
        "shop_name": log.usage.shop.name,
        "feature": log.usage.feature_id,
        "feature_code": log.usage.feature.code,
        "feature_name": log.usage.feature.name,
        "previous_value": log.previous_value,
        "new_value": log.new_value,
        "reason": log.reason,
        "reset_by": log.reset_by_id,
        "reset_by_name": reset_by_name,
        "reset_at": log.reset_at,
    }
