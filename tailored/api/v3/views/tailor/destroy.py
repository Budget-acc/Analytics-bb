from tailored.models import Tailor
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Delete tailor member",
    description="Soft deletes a tailor member in the active shop.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor id.")],
    responses={
        200: OpenApiResponse(description="Exact success message: Tailor member deleted successfully."),
        404: OpenApiResponse(description="Exact error message: Tailor not found."),
    },
    examples=[
        OpenApiExample("Delete success", value={"status": "success", "message": "Tailor member deleted successfully.", "data": None}, response_only=True, status_codes=["200"]),
    ],
)
def destroy(self, request, pk=None):
    """Delete a tailor member"""
    try:
        with transaction.atomic():
            tailor = Tailor.objects.select_for_update().get(pk=pk, shop_id=self.shop_id)
            tailor.is_deleted = True
            tailor.save()

    except Tailor.DoesNotExist:
        return self.not_found_response(
            resource="Tailor",
            message="Tailor not found.",
            dev_message=f"Tailor {pk} not found in shop {self.shop_id}"
        )

    return self.success_response(
        data=None,
        message="Tailor member deleted successfully.",
        dev_message=f"Deleted tailor {pk} from shop {self.shop_id}"
    )
