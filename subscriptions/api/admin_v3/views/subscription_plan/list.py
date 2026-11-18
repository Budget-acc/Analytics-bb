from rest_framework import status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Q
from subscriptions.models import SubscriptionPlan
from subscriptions.api.admin_v3.serializers import SubscriptionPlanSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="List subscription plans",
    description="Returns paginated subscription plans with optional search and filters for type and active state.",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search by name, description, or plan_type."),
        OpenApiParameter("plan_type", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Plan type filter."),
        OpenApiParameter("is_active", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="true/false or 1/0."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated list response."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Invalid plan_type. Must be one of: {valid plan types}\n"
                "- Invalid is_active value. Must be 'true' or 'false'"
            )
        ),
        500: OpenApiResponse(description="Exact message: An error occurred while retrieving subscription plans."),
    },
    examples=[
        OpenApiExample("Request example", value={"search": "pro", "plan_type": "MONTHLY", "is_active": True, "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 2, "name": "Pro", "plan_type": "MONTHLY", "base_price": "999.00"}]}, response_only=True, status_codes=["200"]),
    ],
)
def list(self, request):
    """
    List all subscription plans with optional search and filtering
    """
    try:
        search = request.query_params.get("search", "").strip()
        plan_type = request.query_params.get("plan_type", "").strip()
        is_active = request.query_params.get("is_active", "").strip()

        queryset = SubscriptionPlan.objects.all()

        # Search filtering
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search) |
                Q(plan_type__icontains=search)
            )

        # Plan type filtering
        if plan_type:
            valid_types = [choice[0] for choice in SubscriptionPlan.PLAN_TYPE_CHOICES]
            if plan_type.upper() not in valid_types:
                return Response(
                    {"error": f"Invalid plan_type. Must be one of: {', '.join(valid_types)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(plan_type=plan_type.upper())

        # Active status filtering
        if is_active:
            if is_active.lower() in ['true', '1']:
                queryset = queryset.filter(is_active=True)
            elif is_active.lower() in ['false', '0']:
                queryset = queryset.filter(is_active=False)
            else:
                return Response(
                    {"error": "Invalid is_active value. Must be 'true' or 'false'"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        queryset = queryset.order_by("-created_at")

        # Pagination
        paginator = PageNumberPagination()
        paginated_qs = paginator.paginate_queryset(queryset, request)
        serializer = SubscriptionPlanSerializer(paginated_qs, many=True)

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.exception("Error in SubscriptionPlanViewSet.list")
        return Response(
            {"error": "An error occurred while retrieving subscription plans."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
