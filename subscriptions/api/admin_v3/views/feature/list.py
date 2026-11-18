from rest_framework import status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Q
from subscriptions.models import Feature
from subscriptions.api.admin_v3.serializers import FeatureSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Subscriptions Admin Management V3"],
    summary="List features",
    description="Returns paginated features with search, type, usage, active-state, and price range filters.",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search by name, code, description, feature_type."),
        OpenApiParameter("feature_type", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Feature type filter."),
        OpenApiParameter("usage_type", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Usage type filter."),
        OpenApiParameter("is_active", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="true/false or 1/0."),
        OpenApiParameter("min_price", OpenApiTypes.NUMBER, OpenApiParameter.QUERY, required=False, description="Minimum base price."),
        OpenApiParameter("max_price", OpenApiTypes.NUMBER, OpenApiParameter.QUERY, required=False, description="Maximum base price."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated feature list response."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Invalid feature_type. Must be one of: {valid feature types}\n"
                "- Invalid usage_type. Must be one of: {valid usage types}\n"
                "- Invalid is_active value. Must be 'true' or 'false'\n"
                "- Min price cannot be negative\n"
                "- Invalid min_price format\n"
                "- Max price cannot be negative\n"
                "- Invalid max_price format"
            )
        ),
        500: OpenApiResponse(description="Exact message: An error occurred while retrieving features."),
    },
    examples=[
        OpenApiExample("Request example", value={"search": "REPORT", "feature_type": "ADDON", "usage_type": "COUNT", "min_price": 10, "max_price": 1000, "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 4, "name": "Advanced Reports", "code": "ADV_REPORTS", "base_price": "99.00"}]}, response_only=True, status_codes=["200"]),
    ],
)
def list(self, request):
    """
    List all features with optional search and filtering
    """
    try:
        search = request.query_params.get("search", "").strip()
        feature_type = request.query_params.get("feature_type", "").strip()
        usage_type = request.query_params.get("usage_type", "").strip()
        is_active = request.query_params.get("is_active", "").strip()
        min_price = request.query_params.get("min_price", "").strip()
        max_price = request.query_params.get("max_price", "").strip()

        queryset = Feature.objects.all()

        # Search filtering
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(code__icontains=search) |
                Q(description__icontains=search) |
                Q(feature_type__icontains=search)
            )

        # Feature type filtering
        if feature_type:
            valid_types = [choice[0] for choice in Feature.FEATURE_TYPE_CHOICES]
            if feature_type.upper() not in valid_types:
                return Response(
                    {"error": f"Invalid feature_type. Must be one of: {', '.join(valid_types)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(feature_type=feature_type.upper())

        # Usage type filtering
        if usage_type:
            valid_usage_types = [choice[0] for choice in Feature.USAGE_TYPE_CHOICES]
            if usage_type.upper() not in valid_usage_types:
                return Response(
                    {"error": f"Invalid usage_type. Must be one of: {', '.join(valid_usage_types)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(usage_type=usage_type.upper())

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

        # Price range filtering
        if min_price:
            try:
                min_price = float(min_price)
                if min_price < 0:
                    return Response(
                        {"error": "Min price cannot be negative"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                queryset = queryset.filter(base_price__gte=min_price)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid min_price format"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        if max_price:
            try:
                max_price = float(max_price)
                if max_price < 0:
                    return Response(
                        {"error": "Max price cannot be negative"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                queryset = queryset.filter(base_price__lte=max_price)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid max_price format"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        queryset = queryset.order_by("-created_at")

        # Pagination
        paginator = PageNumberPagination()
        paginated_qs = paginator.paginate_queryset(queryset, request)
        serializer = FeatureSerializer(paginated_qs, many=True)

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.exception("Error in FeatureViewSet.list")
        return Response(
            {"error": "An error occurred while retrieving features."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
