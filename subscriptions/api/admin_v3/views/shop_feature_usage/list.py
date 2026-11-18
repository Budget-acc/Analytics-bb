import logging
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import ShopFeatureUsage

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="List shop feature usage",
    description="Returns paginated feature usage records with optional filters.",
    parameters=[
        OpenApiParameter("shop_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Filter by shop id."),
        OpenApiParameter("feature_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Filter by feature id."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated usage list response."),
        500: OpenApiResponse(description="Exact message: Unable to fetch feature usage."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "count": 1, "next": None, "previous": None,
                "results": [{
                    "id": 1, "shop": 3, "shop_name": "ABC Boutique",
                    "feature": 4, "feature_code": "WHATSAPP_MSG", "feature_name": "WhatsApp Messaging",
                    "used": 12, "period_start": "2026-06-01T00:00:00Z", "period_end": "2026-06-30T23:59:59Z",
                }],
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def list(self, request):
    try:
        shop_id = request.query_params.get("shop_id")
        feature_id = request.query_params.get("feature_id")

        qs = (
            ShopFeatureUsage.objects
            .select_related("shop", "feature")
            .order_by("-id")
        )

        if shop_id:
            qs = qs.filter(shop_id=shop_id)
        if feature_id:
            qs = qs.filter(feature_id=feature_id)

        paginator = PageNumberPagination()
        items = paginator.paginate_queryset(qs, request)

        data = [_serialize(i) for i in items]

        return paginator.get_paginated_response(data)

    except Exception as e:
        logger.error(f"ShopFeatureUsageViewSet.list:\n{e}", exc_info=True)
        return self.error_response(
            message="Unable to fetch feature usage.",
            dev_message=str(e),
        )


def _serialize(obj):
    return {
        "id": obj.id,
        "shop": obj.shop_id,
        "shop_name": obj.shop.name,
        "feature": obj.feature_id,
        "feature_code": obj.feature.code,
        "feature_name": obj.feature.name,
        "used": obj.used,
        "period_start": obj.period_start,
        "period_end": obj.period_end,
    }
