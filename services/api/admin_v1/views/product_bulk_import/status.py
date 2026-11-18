import logging

from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.utils import timezone

from services.models import BulkProductImport, BulkProductImportItem

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Get bulk product import status",
    description=(
        "Returns job-level progress/counts for a bulk Google Drive product import, plus a "
        "paginated breakdown of non-successful items (failed/invalid/duplicate) - successful "
        "items are only counted, never listed individually, so the response stays small even "
        "for imports of tens of thousands of images."
    ),
    parameters=[
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Bulk import id."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Page number for the item breakdown."),
    ],
    responses={
        200: OpenApiResponse(description="Import status retrieved."),
        404: OpenApiResponse(description="Exact message: Bulk import not found."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Import status retrieved.",
                "data": {
                    "import_id": 42, "status": "processing", "total_items": 10000,
                    "processed_count": 4000, "success_count": 3950, "failed_count": 30, "skipped_count": 20,
                    "items": {"page": 1, "page_size": 50, "total_pages": 1, "total_count": 50, "results": []},
                },
            },
            response_only=True, status_codes=["200"],
        ),
    ],
)
@action(detail=True, methods=["get"], url_path="status")
def status(self, request, pk=None):
    try:
        try:
            bulk_import = BulkProductImport.objects.select_related("shop", "shop_service").get(id=pk)
        except BulkProductImport.DoesNotExist:
            return Response(
                {"status": "error", "message": "Bulk import not found.", "data": None},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        avg_seconds_per_item = None
        estimated_seconds_remaining = None
        if bulk_import.started_at and bulk_import.processed_count > 0:
            elapsed = (timezone.now() - bulk_import.started_at).total_seconds()
            avg_seconds_per_item = round(elapsed / bulk_import.processed_count, 3)
            remaining_items = max(bulk_import.total_items - bulk_import.processed_count, 0)
            estimated_seconds_remaining = round(avg_seconds_per_item * remaining_items, 1)

        non_success_items = BulkProductImportItem.objects.filter(
            bulk_import=bulk_import
        ).exclude(status="created").order_by("id")

        paginator = PageNumberPagination()
        paginator.page_size = 50
        try:
            page = paginator.paginate_queryset(non_success_items, request)
        except NotFound:
            return Response(
                {"status": "error", "message": "Invalid page.", "data": None},
                status=http_status.HTTP_404_NOT_FOUND,
            )
        item_results = [
            {
                "id": item.id,
                "filename": item.filename,
                "status": item.status,
                "error_message": item.error_message,
                "attempt_count": item.attempt_count,
            }
            for item in page
        ]

        data = {
            "import_id": bulk_import.id,
            "status": bulk_import.status,
            "drive_folder_id": bulk_import.drive_folder_id,
            "shop_id": bulk_import.shop_id,
            "shop_service_id": bulk_import.shop_service_id,
            "default_quantity": bulk_import.default_quantity,
            "total_items": bulk_import.total_items,
            "processed_count": bulk_import.processed_count,
            "success_count": bulk_import.success_count,
            "failed_count": bulk_import.failed_count,
            "skipped_count": bulk_import.skipped_count,
            "avg_seconds_per_item": avg_seconds_per_item,
            "estimated_seconds_remaining": estimated_seconds_remaining,
            "error_message": bulk_import.error_message or None,
            "created_at": bulk_import.created_at.isoformat(),
            "started_at": bulk_import.started_at.isoformat() if bulk_import.started_at else None,
            "completed_at": bulk_import.completed_at.isoformat() if bulk_import.completed_at else None,
            "items": {
                "page": int(request.query_params.get("page", 1)),
                "page_size": paginator.page_size,
                "total_pages": paginator.page.paginator.num_pages,
                "total_count": paginator.page.paginator.count,
                "results": item_results,
            },
        }

        return Response(
            {"status": "success", "message": "Import status retrieved.", "data": data},
            status=http_status.HTTP_200_OK,
        )

    except Exception as e:
        logger.error(f"[Bulk Import Status] Error: {str(e)}", exc_info=True)
        return Response(
            {"status": "error", "message": "Unable to retrieve import status.", "data": None},
            status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
