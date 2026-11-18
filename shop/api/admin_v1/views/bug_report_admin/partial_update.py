from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from shop.models import BugReport
from shop.api.admin_v1.serializers import BugReportStatusUpdateSerializer
from django.utils import timezone
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Update bug report status",
    description="Transitions bug report status using restricted transition rules.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Bug report id.")],
    request=inline_serializer(
        name="ShopAdminV1BugReportPatchRequest",
        fields={
            "status": serializers.CharField(required=True, help_text="OPEN | IN_PROGRESS | RESOLVED"),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact messages: No change needed. / Status updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Invalid status input.\n"
                "- Status transition blocked.\n"
                "- Cannot transition from {old_status} to {new_status}."
            )
        ),
        404: OpenApiResponse(description="Exact messages: Bug report not found. / Bug report {pk} not found."),
        500: OpenApiResponse(description="Exact message: Failed to update bug status."),
    },
    examples=[
        OpenApiExample("Request example", value={"status": "IN_PROGRESS"}, request_only=True),
        OpenApiExample("Success response", value={"message": "Status updated successfully.", "dev_message": "Changed from OPEN → IN_PROGRESS", "data": {"id": 7, "status": "IN_PROGRESS", "resolved_at": None}}, response_only=True, status_codes=["200"]),
    ],
)
def partial_update(self, request, pk=None):
    """
    PATCH /admin/bugreports/{pk}/
    Body: { "status": "RESOLVED" }
    """
    serializer = BugReportStatusUpdateSerializer(data=request.data)
    if not serializer.is_valid():
        return self.validation_error_response(
            errors=serializer.errors,
            message="Invalid status input."
        )

    new_status = serializer.validated_data["status"]

    try:
        with transaction.atomic():
            bug = BugReport.objects.select_for_update().get(pk=pk)

            old_status = bug.status

            if old_status == new_status:
                return Response({
                    "message": "No change needed.",
                    "data": {"id": bug.id, "status": bug.status}
                })

            allowed_transitions = {
                "OPEN": {"IN_PROGRESS", "RESOLVED"},
                "IN_PROGRESS": {"RESOLVED", "OPEN"},
                "RESOLVED": set(),
            }

            if new_status not in allowed_transitions.get(old_status, set()):
                return self.validation_error_response(
                    errors={"status": [f"Cannot transition from {old_status} to {new_status}."]},
                    message=f"Status transition blocked."
                )

            # Update status
            bug.status = new_status

            if new_status == "RESOLVED" and not bug.resolved_at:
                bug.resolved_at = timezone.now()
                bug.save(update_fields=["status", "resolved_at"])
            else:
                bug.save(update_fields=["status"])

            bug.refresh_from_db()

    except BugReport.DoesNotExist:
        return self.not_found_response(resource="BugReport", message="Bug report not found.")
    except Exception as e:
        logger.exception("Failed to update bug status.")
        return self.error_response(
            message="Failed to update bug status.",
            dev_message=str(e)
        )

    return Response({
        "message": "Status updated successfully.",
        "dev_message": f"Changed from {old_status} → {new_status}",
        "data": {
            "id": bug.id,
            "status": bug.status,
            "resolved_at": bug.resolved_at.isoformat() if bug.resolved_at else None,
        }
    })
