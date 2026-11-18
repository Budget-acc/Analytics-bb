from rest_framework import status
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from shop.models import Shop, BugReport
from ERP.utils.s3_image_uploads import upload_files_to_s3
from django.core.exceptions import ValidationError as DjangoValidationError
import boto3
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["Shop Management V3"],
    summary="Create bug report",
    description="Creates a shop bug report with optional images and optional JSON more_details.",
    request=inline_serializer(
        name="ShopV3BugReportCreateRequest",
        fields={
            "title": serializers.CharField(required=True),
            "description": serializers.CharField(required=False, allow_blank=True),
            "more_details": serializers.JSONField(required=False),
            "images": serializers.ListField(required=False, child=serializers.ImageField(), help_text="Up to 5 images, each <=3MB."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Bug reported successfully. Our team will review it shortly."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Please provide a short title for the bug.\n"
                "- Additional details are too large. Please reduce the size.\n"
                "- Additional details must be a valid JSON object.\n"
                "- Additional details must be a JSON object.\n"
                "- Invalid images uploaded.\n"
                "- Invalid input. Please check your data.\n"
                "- This field is required.\n"
                "- Additional details too large. Maximum 10KB allowed.\n"
                "- Invalid JSON format. Must be a valid JSON object.\n"
                "- Invalid format."
            )
        ),
        404: OpenApiResponse(description="Exact message: Shop not found."),
        500: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Failed to upload images. Please try again later.\n"
                "- Failed to save bug report. Please try again.\n"
                "- An unexpected error occurred while reporting the bug. Please try again later."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"title": "Crash while saving", "description": "App crashed after clicking save", "more_details": {"screen": "booking"}}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Bug reported successfully. Our team will review it shortly.", "data": {"id": 15, "shop_id": 4, "title": "Crash while saving", "status": "OPEN"}}, response_only=True, status_codes=["200"]),
    ],
)
def create(self, request):
    """
    Create a bug report. Returns 201 with created object summary,
    or appropriate error JSON with keys: message (user-friendly), dev_message (developer).
    """
    try:
        try:
            shop = Shop.objects.get(id=self.shop_id)
        except Shop.DoesNotExist:
            return self.error_response(
                message="Shop not found.",
                dev_message=f"Shop with id {getattr(self, 'shop_id', None)} does not exist.",
                status_code=status.HTTP_404_NOT_FOUND
            )

        reporter = request.user

        data = request.data

        title = (data.get("title") or "").strip()
        description = (data.get("description") or "").strip()
        more_details_raw = data.get("more_details")

        if not title:
            return self.validation_error_response(
                errors={"title": ["This field is required."]},
                message="Please provide a short title for the bug."
            )

        if more_details_raw and len(str(more_details_raw)) > 10000:
            return self.validation_error_response(
                errors={"more_details": ["Additional details too large. Maximum 10KB allowed."]},
                message="Additional details are too large. Please reduce the size."
            )

        more_details = None
        if more_details_raw:
            if isinstance(more_details_raw, str):
                import json
                try:
                    more_details = json.loads(more_details_raw)
                    if not isinstance(more_details, dict):
                        raise ValueError("Expected JSON object")
                except (json.JSONDecodeError, ValueError):
                    return self.validation_error_response(
                        errors={"more_details": ["Invalid JSON format. Must be a valid JSON object."]},
                        message="Additional details must be a valid JSON object."
                    )
            elif isinstance(more_details_raw, dict):
                more_details = more_details_raw
            else:
                return self.validation_error_response(
                    errors={"more_details": ["Invalid format."]},
                    message="Additional details must be a JSON object."
                )

        files = request.FILES.getlist("images") or request.FILES.getlist("images[]") or []

        if not files:
            files = [file for key, file in request.FILES.items() if key.startswith("images")]

        uploaded_objects = []
        if files:
            try:
                uploaded_objects = upload_files_to_s3(
                    files=files,
                    shop=shop,
                    reporter=reporter,
                    subfolder="bug_reports",
                    max_files=5,
                    max_size_bytes=3 * 1024 * 1024,
                    max_image_dims=(1600, 1600),
                )
            except ValueError as ve:
                return self.validation_error_response(
                    errors={"images": [str(ve)]},
                    message="Invalid images uploaded.",
                    dev_message=str(ve)
                )
            except Exception as e:
                logger.error(f"S3 upload failed for bug report images: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to upload images. Please try again later.",
                    dev_message=str(e)
                )

        try:
            with transaction.atomic():
                bug = BugReport.objects.create(
                    shop=shop,
                    reporter=reporter,
                    title=title,
                    description=description,
                    more_details=more_details,
                    images=[obj["url"] for obj in uploaded_objects],
                    status="OPEN",
                )
        except Exception as e:

            if uploaded_objects:
                try:
                    s3 = boto3.client(
                        "s3",
                        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                        region_name=settings.AWS_S3_REGION_NAME,
                    )
                    for obj in uploaded_objects:
                        s3.delete_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=obj["key"])
                except Exception as cleanup_err:
                    logger.error(f"Failed to cleanup uploaded images after DB failure: {cleanup_err}")

            logger.exception("Failed to save BugReport to DB.")
            return self.error_response(
                message="Failed to save bug report. Please try again.",
                dev_message=str(e)
            )

        response_data = {
            "id": bug.id,
            "shop_id": bug.shop_id,
            "reporter_id": bug.reporter_id,
            "title": bug.title,
            "description": bug.description,
            "more_details": bug.more_details,
            "images": bug.images,
            "status": bug.status,
            "created_at": timezone.localtime(bug.created_at).strftime("%d-%m-%Y %H:%M:%S"),
        }

        return self.success_response(
            message= "Bug reported successfully. Our team will review it shortly.",
            dev_message= "BugReport created",
            data= response_data
        )

    except DjangoValidationError as ve:
        logger.error(f"Validation error in BugReport create: {ve}")
        return self.validation_error_response(
            errors=ve.message_dict if hasattr(ve, "message_dict") else {"detail": str(ve)},
            message="Invalid input. Please check your data."
        )

    except Exception as e:
        logger.error(f"Unexpected error while creating bug report: {str(e)}", exc_info=True)
        return self.error_response(
            message="An unexpected error occurred while reporting the bug. Please try again later.",
            dev_message=str(e),
        )
