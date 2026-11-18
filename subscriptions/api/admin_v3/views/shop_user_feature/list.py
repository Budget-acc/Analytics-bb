from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.models import ShopUserFeature
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="List shop-user feature access",
    description="Returns paginated feature access records for shop users with optional filters.",
    parameters=[
        OpenApiParameter("shop_user", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Filter by shop_user id."),
        OpenApiParameter("user_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Filter by global user id."),
        OpenApiParameter("feature", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Filter by feature id."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated access list response."),
        500: OpenApiResponse(description="Exact message: Unable to fetch user feature access."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 14, "user": 25, "shop_user": 81, "feature": 4, "feature_code": "ADV_REPORTS", "feature_name": "Advanced Reports", "granted": True}]}, response_only=True, status_codes=["200"]),
    ],
)
def list(self, request):
    try:
        shop_user = request.query_params.get("shop_user")
        user_id = request.query_params.get("user_id")
        feature = request.query_params.get("feature")

        qs = (
            ShopUserFeature.objects
            .select_related("shop_user", "feature")
            .order_by("-id")
        )

        if shop_user:
            qs = qs.filter(shop_user_id=shop_user)
        if user_id:
            qs = qs.filter(shop_user__user_id=user_id)
        if feature:
            qs = qs.filter(feature_id=feature)

        paginator = PageNumberPagination()
        items = paginator.paginate_queryset(qs, request)

        data = [{
            "id": i.id,
            "user": i.shop_user.user_id,
            "shop_user": i.shop_user_id,
            "feature": i.feature_id,
            "feature_code": i.feature.code,
            "feature_name": i.feature.name,
            "granted": i.granted,
        } for i in items]

        return paginator.get_paginated_response(data)

    except Exception as e:
        logger.error(f"ShopUserFeatureViewSet.list:\n,{e}")
        return self.error_response(
            message="Unable to fetch user feature access.",
            dev_message=str(e)
        )
