import logging

from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.utils import timezone

from services.models import BulkProductImport

logger = logging.getLogger("api_requests")

TERMINAL_STATUSES = ["completed", "completed_with_errors", "failed", "cancelled"]


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Cancel a running bulk product import",
    description=(
        "Requests cancellation of a bulk Google Drive product import. Cancellation is "
        "best-effort: already-dispatched chunk tasks finish their current chunk (already "
        "processed/uploaded images in that chunk are still committed) but no further chunks "
        "are dispatched."
    ),
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Bulk import id.")],
    responses={
        200: OpenApiResponse(description="Exact message: Cancellation requested. In-flight chunks will stop after their current batch."),
        400: OpenApiResponse(description="Exact message: This import has already completed and cannot be cancelled."),
        404: OpenApiResponse(description="Exact message: Bulk import not found."),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Cancellation requested. In-flight chunks will stop after their current batch.", "data": {"import_id": 42, "status": "cancelled"}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=True, methods=["post"], url_path="cancel")
def cancel(self, request, pk=None):
    try:
        try:
            bulk_import = BulkProductImport.objects.get(id=pk)
        except BulkProductImport.DoesNotExist:
            return Response(
                {"status": "error", "message": "Bulk import not found.", "data": None},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        if bulk_import.status in TERMINAL_STATUSES:
            return Response(
                {
                    "status": "error",
                    "message": "This import has already completed and cannot be cancelled.",
                    "data": {"import_id": bulk_import.id, "status": bulk_import.status},
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        bulk_import.cancel_requested = True
        bulk_import.status = "cancelled"
        bulk_import.completed_at = timezone.now()
        bulk_import.save(update_fields=["cancel_requested", "status", "completed_at"])

        return Response(
            {
                "status": "success",
                "message": "Cancellation requested. In-flight chunks will stop after their current batch.",
                "data": {"import_id": bulk_import.id, "status": bulk_import.status},
            },
            status=http_status.HTTP_200_OK,
        )

    except Exception as e:
        logger.error(f"[Bulk Import Cancel] Error: {str(e)}", exc_info=True)
        return Response(
            {"status": "error", "message": "Unable to cancel import.", "data": None},
            status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
