from rest_framework import status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import MainServiceCategory
from services.api.admin_v1.serializers import MainServiceCategorySerializer


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="List main service categories",
    description="Returns paginated list of main service categories.",
    parameters=[OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number.")],
    responses={
        200: OpenApiResponse(description="Paginated main service category list response."),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 1, "name": "Clothing", "icon": "https://...", "is_active": True}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Server error response", value={"error": "Database error"}, response_only=True, status_codes=["500"]),
    ],
)
def list(self, request):
    try:
        categories = MainServiceCategory.objects.all()
        paginator = PageNumberPagination()
        paginated = paginator.paginate_queryset(categories, request)
        serializer = MainServiceCategorySerializer(paginated, many=True)
        return paginator.get_paginated_response(serializer.data)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
