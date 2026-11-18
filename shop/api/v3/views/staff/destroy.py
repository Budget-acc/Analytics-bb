from django.db import transaction
from shop.models import Staff
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema


@extend_schema(
    tags=["Shop Management V3"],
    summary="Soft delete staff",
    description="Soft deletes a staff member (sets is_deleted=true) in active shop.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Staff id.")],
    responses={
        200: OpenApiResponse(description="Exact message: Staff member deleted successfully."),
        404: OpenApiResponse(description="Exact message: Staff not found."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Staff member deleted successfully.", "data": None}, response_only=True, status_codes=["200"]),
    ],
)
def destroy(self, request, pk=None):
    """Delete a staff member"""
    try:
        with transaction.atomic():
            staff = Staff.objects.select_for_update().prefetch_related("bookings").get(pk=pk, shop_id=self.shop_id)
            # if staff.bookings.exists():
            #     return self.validation_error_response(
            #         errors={"staff": ["Staff member has assigned bookings and cannot be deleted."]},
            #         message="Cannot delete staff with active bookings.",
            #         dev_message=f"Attempted to delete staff {pk} with {staff.bookings.count()} booking(s)"
            #     )

            # staff.delete()
            staff.is_deleted = True
            staff.save()

    except Staff.DoesNotExist:
        return self.not_found_response(
            resource="Staff",
            message="Staff not found.",
            dev_message=f"Staff {pk} not found in shop {self.shop_id}"
        )

    return self.success_response(
        data=None,
        message="Staff member deleted successfully.",
        dev_message=f"Deleted staff {pk} from shop {self.shop_id}"
    )
