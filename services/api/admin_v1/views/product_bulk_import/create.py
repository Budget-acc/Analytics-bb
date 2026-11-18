import logging

from rest_framework import status, serializers
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from django.conf import settings

from services.models import BulkProductImport, ShopOwnerService
from services.utils.google_drive import validate_folder_access, DriveAccessError
from services.tasks.bulk_import_tasks import list_and_queue_bulk_import_task

logger = logging.getLogger("api_requests")

ACTIVE_STATUSES = ["pending", "validating", "listing", "processing"]


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Start bulk product import from Google Drive",
    description=(
        "Creates a background job that reads every image in a Google Drive folder and creates "
        "one Product (+ a single default variant) per image. Processing happens entirely in "
        "background Celery tasks - this endpoint only validates input and returns immediately "
        "with a job id to poll via the status endpoint.\n\n"
        "drive_folder_id, shop_id, shop_service_id, and default_quantity are all request-body "
        "parameters (not server/env configuration), so the same endpoint handles any "
        "shop/service/folder combination without a restart."
    ),
    request=inline_serializer(
        name="ProductBulkImportCreateRequest",
        fields={
            "drive_folder_id": serializers.CharField(required=True, help_text="Google Drive folder ID containing the product images."),
            "shop_id": serializers.IntegerField(required=True),
            "shop_service_id": serializers.IntegerField(required=True, help_text="Target shop service id. Must belong to shop_id."),
            "default_quantity": serializers.IntegerField(required=False, help_text="Stock for each created product's default variant. Default: 1."),
            "target_resolution": serializers.IntegerField(required=False, help_text="Override the default main-image resolution (px, square)."),
            "max_image_bytes": serializers.IntegerField(required=False, help_text="Override the default max main-image size in bytes."),
            "jpeg_quality_start": serializers.IntegerField(required=False),
            "jpeg_quality_min": serializers.IntegerField(required=False),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Bulk import started."),
        400: OpenApiResponse(description="Validation errors: missing fields, invalid/inaccessible Drive folder."),
        404: OpenApiResponse(description="Exact message: The selected service is not available."),
        409: OpenApiResponse(description="Exact message: An import for this folder is already in progress."),
    },
    examples=[
        OpenApiExample("Request example", value={"drive_folder_id": "1A2b3C4d5E6f7G8h9I0jKlmNoPQRstuv", "shop_id": 7, "shop_service_id": 23, "default_quantity": 5}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Bulk import started.", "data": {"import_id": 42, "status": "pending", "status_url": "/api/admin_v1/admin/product-bulk-import/42/status/"}}, response_only=True, status_codes=["201"]),
    ],
)
def create(self, request):
    try:
        data = request.data

        drive_folder_id = (data.get("drive_folder_id") or "").strip()
        shop_id = data.get("shop_id")
        shop_service_id = data.get("shop_service_id")

        if not drive_folder_id or not shop_id or not shop_service_id:
            return Response(
                {
                    "status": "error",
                    "message": "drive_folder_id, shop_id, and shop_service_id are required.",
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            default_quantity = int(data.get("default_quantity") or 1)
            if default_quantity < 1:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {"status": "error", "message": "default_quantity must be a positive integer.", "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            shop_service = ShopOwnerService.objects.select_related("shop", "general_service").get(
                id=shop_service_id, shop_id=shop_id, deleted__isnull=True,
            )
        except ShopOwnerService.DoesNotExist:
            return Response(
                {"status": "error", "message": "The selected service is not available.", "data": None},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Idempotency: don't start a duplicate import for the same shop+folder
        # while one is already active.
        existing = BulkProductImport.objects.filter(
            shop_id=shop_id, drive_folder_id=drive_folder_id, status__in=ACTIVE_STATUSES,
        ).first()
        if existing:
            return Response(
                {
                    "status": "error",
                    "message": "An import for this folder is already in progress.",
                    "data": {
                        "import_id": existing.id,
                        "status": existing.status,
                        "status_url": f"/api/v1/service/admin/product-bulk-import/{existing.id}/status/",
                    },
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Validate Drive access BEFORE creating any job row - fail fast.
        try:
            validate_folder_access(drive_folder_id)
        except DriveAccessError as e:
            return Response(
                {"status": "error", "message": str(e), "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        bulk_import = BulkProductImport.objects.create(
            shop_id=shop_id,
            shop_service=shop_service,
            created_by=request.user if request.user and request.user.is_authenticated else None,
            drive_folder_id=drive_folder_id,
            default_quantity=default_quantity,
            target_resolution=data.get("target_resolution") or getattr(settings, "BULK_IMPORT_TARGET_RESOLUTION", 1200),
            max_image_bytes=data.get("max_image_bytes") or getattr(settings, "BULK_IMPORT_MAX_IMAGE_BYTES", 2_500_000),
            jpeg_quality_start=data.get("jpeg_quality_start") or getattr(settings, "BULK_IMPORT_JPEG_QUALITY_START", 90),
            jpeg_quality_min=data.get("jpeg_quality_min") or getattr(settings, "BULK_IMPORT_JPEG_QUALITY_MIN", 35),
        )

        list_and_queue_bulk_import_task.delay(bulk_import.id)

        return Response(
            {
                "status": "success",
                "message": "Bulk import started.",
                "data": {
                    "import_id": bulk_import.id,
                    "status": bulk_import.status,
                    "status_url": f"/api/admin_v1/admin/product-bulk-import/{bulk_import.id}/status/",
                },
            },
            status=status.HTTP_201_CREATED,
        )

    except Exception as e:
        logger.error(f"[Bulk Import Create] Unexpected error: {str(e)}", exc_info=True)
        return Response(
            {"status": "error", "message": "Something went wrong while starting the import.", "data": None},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
