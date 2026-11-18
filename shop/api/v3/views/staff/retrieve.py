from shop.models import Staff
from shop.api.v3.serializers import StaffSerializer
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

@extend_schema(
    tags=["Shop Management V3"],
    summary="Retrieve staff",
    description="Returns one staff member by id for active shop.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Staff id.")],
    responses={
        200: OpenApiResponse(description="Standard success response with staff data."),
        404: OpenApiResponse(description="Exact message: Staff not found."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Staff details retrieved successfully.", "data": {"id": 10, "name": "Raju"}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found", value={"status": "not_found", "message": "Staff not found.", "data": None}, response_only=True, status_codes=["404"]),
    ],
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    """Retrieve a single staff member"""
    try:
        staff = Staff.objects.get(pk=pk, shop_id=self.shop_id)
    except Staff.DoesNotExist:
        return self.not_found_response(
            resource="Staff",
            message="Staff not found.",
            dev_message=f"Staff {pk} not found in shop {self.shop_id}"
        )
    serializer = StaffSerializer(staff)
    return self.success_response(
        data=serializer.data,
        message="Staff details retrieved successfully.",
        dev_message=f"Retrieved staff {pk} for shop {self.shop_id}"
    )
