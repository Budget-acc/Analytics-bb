from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Shop


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Delete shop",
    description="Deletes a shop by id.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop id.")],
    responses={
        204: OpenApiResponse(description="Exact message in body: Shop deleted successfully."),
        404: OpenApiResponse(description="Exact message: Shop not found."),
    },
    examples=[
        OpenApiExample("Success response", value={"message": "Shop deleted successfully."}, response_only=True, status_codes=["204"]),
        OpenApiExample("Not found response", value={"error": "Shop not found."}, response_only=True, status_codes=["404"]),
    ],
)
def destroy(self, request, pk=None):
    try:
        shop = Shop.objects.get(pk=pk)
        shop.delete()
        return Response({"message": "Shop deleted successfully."}, status=status.HTTP_204_NO_CONTENT)
    except Shop.DoesNotExist:
        return Response({"error": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)
