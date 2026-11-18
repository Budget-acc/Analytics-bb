from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import MainServiceCategory


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Delete main service category",
    description="Deletes a main service category by id.",
    parameters=[OpenApiParameter(name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Main service category id.")],
    responses={
        200: OpenApiResponse(description="Exact message: Main service category deleted successfully."),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Success response", value={"message": "Main service category deleted successfully."}, response_only=True, status_codes=["200"]),
        OpenApiExample("Server error response", value={"error": "Cannot delete due to related objects"}, response_only=True, status_codes=["500"]),
    ],
)
def destroy(self, request, pk=None):
    try:
        instance = get_object_or_404(MainServiceCategory, pk=pk)
        instance.delete()
        return Response({"message": "Main service category deleted successfully."}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
