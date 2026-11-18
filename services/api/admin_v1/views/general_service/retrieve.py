from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import GeneralService
from services.api.admin_v1.serializers import GeneralServiceSerializer


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Retrieve general service",
    description="Returns details of a specific general service by id.",
    parameters=[
        OpenApiParameter(name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="General service id."),
    ],
    responses={
        200: OpenApiResponse(description="General service object response."),
        404: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Request example", value={"id": 3}, request_only=True),
        OpenApiExample("Success response", value={"id": 3, "name": "Tailoring", "description": "Tailoring services", "icon": "https://...", "is_active": True, "main_category": 1, "main_category_name": "Clothing"}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found response", value={"error": "No GeneralService matches the given query."}, response_only=True, status_codes=["404"]),
    ],
)
def retrieve(self, request, pk=None):
    try:
        service = get_object_or_404(GeneralService, pk=pk)
        serializer = GeneralServiceSerializer(service)
        return Response(serializer.data)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
