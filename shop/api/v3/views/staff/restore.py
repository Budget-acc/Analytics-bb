from rest_framework.decorators import action
from django.db import transaction
from shop.models import Staff
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema


@extend_schema(
    tags=["Shop Management V3"],
    summary="Restore soft-deleted staff",
    description="Restores a previously soft-deleted staff member.",
    parameters=[OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Staff id to restore.")],
    responses={
        200: OpenApiResponse(description="Exact message: Staff member restored successfully."),
        404: OpenApiResponse(description="Exact message: Staff not found or already active."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Staff member restored successfully.", "data": {"id": 10, "name": "Raju", "phone": "9999999999"}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["patch"], url_path="restore/(?P<pk>[^/.]+)")
def restore(self, request, *args, **kwargs):
    """Restore a previously soft-deleted staff member"""
    try:
        pk = kwargs.get("pk")
        with transaction.atomic():
            staff = Staff.objects.select_for_update().get(
                pk=pk, shop_id=self.shop_id, is_deleted=True
            )
            staff.is_deleted = False
            staff.save()

    except Staff.DoesNotExist:
        return self.not_found_response(
            resource="Staff",
            message="Staff not found or already active.",
            dev_message=f"Staff {pk} not found or already active in shop {self.shop_id}"
        )

    return self.success_response(
        data={"id": staff.id, "name": staff.name, "phone": staff.phone},
        message="Staff member restored successfully.",
        dev_message=f"Restored staff {pk} in shop {self.shop_id}"
    )
