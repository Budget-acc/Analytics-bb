from rest_framework import status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Q
from services.models import GeneralService
from services.api.admin_v1.serializers import GeneralServiceSerializer
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="List general services",
    description="Returns paginated general service categories with optional search by name or description.",
    parameters=[
        OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Optional search term for name/description."),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated general service list response."),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Request example", value={"search": "tailor", "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 3, "name": "Tailoring", "description": "Tailoring services", "icon": "https://...", "is_active": True, "main_category": 1, "main_category_name": "Clothing"}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Server error response", value={"error": "Database connection error"}, response_only=True, status_codes=["500"]),
    ],
)
def list(self, request):
    try:
        services = GeneralService.objects.all()
        search = (request.query_params.get("search") or "").strip()
        if search:
            services = services.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )
        services = services.order_by("-id")

        paginator = PageNumberPagination()
        paginated_services = paginator.paginate_queryset(services, request)
        serializer = GeneralServiceSerializer(paginated_services, many=True)
        return paginator.get_paginated_response(serializer.data)
    except Exception as e:
        return Response(
            {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
