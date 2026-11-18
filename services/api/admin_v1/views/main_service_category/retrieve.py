from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import MainServiceCategory
from services.api.admin_v1.serializers import MainServiceCategorySerializer


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Retrieve main service category",
    description="Returns details of a specific main service category by id.",
    parameters=[OpenApiParameter(name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Main service category id.")],
    responses={
        200: OpenApiResponse(description="Main service category object response."),
        404: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Success response", value={"id": 1, "name": "Clothing", "icon": "https://...", "is_active": True}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found response", value={"error": "No MainServiceCategory matches the given query."}, response_only=True, status_codes=["404"]),
    ],
)
def retrieve(self, request, pk=None):
    try:
        category = get_object_or_404(MainServiceCategory, pk=pk)
        serializer = MainServiceCategorySerializer(category)
        return Response(serializer.data)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
