from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import GeneralService, MainServiceCategory
from services.api.admin_v1.serializers import GeneralServiceSerializer


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="List services by main category",
    description="Returns active general services for a specific main category id.",
    parameters=[
        OpenApiParameter(name="category_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Main category id."),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated general service list response."),
        400: OpenApiResponse(description="Exact message: Invalid category ID format."),
        404: OpenApiResponse(description="Exact message: Main category not found."),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Request example", value={"category_id": 1, "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 5, "name": "Tailoring", "description": "...", "icon": "https://...", "is_active": True, "main_category": 1, "main_category_name": "Clothing"}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Invalid id response", value={"error": "Invalid category ID format."}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["get"], url_path="main-category/(?P<category_id>[^/.]+)")
def by_category(self, request, category_id=None):
    """
    List all services belonging to a specific main category
    """
    try:
        # Validate the category_id
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            return Response(
                {"error": "Invalid category ID format."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if category exists
        try:
            category = MainServiceCategory.objects.get(pk=category_id)
        except MainServiceCategory.DoesNotExist:
            return Response(
                {"error": "Main category not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get services in this category
        services = GeneralService.objects.filter(
            main_category=category,
            is_active=True
        ).order_by('name')

        # Paginate results
        paginator = PageNumberPagination()
        paginated_services = paginator.paginate_queryset(services, request)
        serializer = GeneralServiceSerializer(paginated_services, many=True)

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
