from tailored.models import Tailor
from tailored.api.v3.serializers import TailorSerializer
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Retrieve tailor member",
    description="Fetches one tailor member for the active shop by id.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor id.")],
    responses={
        200: OpenApiResponse(description="Exact success message: Tailor details retrieved successfully."),
        404: OpenApiResponse(description="Exact error message: Tailor not found."),
    },
    examples=[
        OpenApiExample("Retrieve success", value={"status": "success", "message": "Tailor details retrieved successfully.", "data": {"id": 5, "name": "Rahul Tailor", "phone": "+919876543210", "is_deleted": False}}, response_only=True, status_codes=["200"]),
    ],
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    """Retrieve a single tailor"""
    try:
        tailor = Tailor.objects.get(pk=pk, shop_id=self.shop_id)
    except Tailor.DoesNotExist:
        return self.not_found_response(
            resource="Tailor",
            message="Tailor not found.",
            dev_message=f"Tailor {pk} not found in shop {self.shop_id}"
        )
    serializer = TailorSerializer(tailor)
    return self.success_response(
        data=serializer.data,
        message="Tailor details retrieved successfully.",
        dev_message=f"Retrieved tailor {pk} for shop {self.shop_id}"
    )
