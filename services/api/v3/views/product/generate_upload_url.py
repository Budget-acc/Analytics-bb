import uuid
import logging

from django.conf import settings
from ERP.utils.s3_cache_control import presigned_image_cache_control
from django.utils import timezone
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from services.models import ShopOwnerService
from services.api.v3.views.product.constants import ALLOWED_IMAGE_CONTENT_TYPES, ALLOWED_ENTITY_TYPES, PRESIGNED_URL_EXPIRY
from services.api.v3.views.product.helpers.image_helpers import _sanitize_s3_segment
from services.s3_service import S3Service
from shop.api.v3.utils.shop import get_active_shop_id

logger = logging.getLogger("api_requests")



_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Generate S3 upload URL",
    description="Creates a pre-signed S3 PUT URL for product/booking uploads and returns required headers.",
    request=inline_serializer(
        name="ServiceV3GenerateUploadUrlRequest",
        fields={
            "entity_type": serializers.CharField(required=True, help_text="product | booking_document | booking_thumbnail"),
            "content_type": serializers.CharField(required=True, help_text="image/jpeg, image/jpg, image/png, image/webp"),
            "label": serializers.CharField(required=False, allow_blank=True),
            "shop_service_id": serializers.IntegerField(required=False, help_text="Required when entity_type=product"),
            "shop_id": serializers.IntegerField(required=False, help_text="Optional for booking_document/booking_thumbnail"),
            "product_id": serializers.IntegerField(required=False, help_text="Required when entity_type=booking_thumbnail"),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Upload URL generated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Invalid entity type.\n"
                "- Unsupported file type.\n"
                "- shop_service_id is required for product uploads.\n"
                "- product_id is required for booking thumbnail uploads.\n"
                "- Must be one of: ...\n"
                "- Unsupported type. Allowed: image/jpeg, image/jpg, image/png, image/webp."
            )
        ),
        404: OpenApiResponse(description="Not-found messages: The selected service is not available. / Shop not found."),
        500: OpenApiResponse(description="Exact message: Could not generate upload URL. Please try again."),
    },
    examples=[
        OpenApiExample("Request example", value={"entity_type": "product", "content_type": "image/jpeg", "label": "blue-shirt", "shop_service_id": 8}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Upload URL generated successfully.", "data": {"upload_url": "https://...", "file_url": "https://...", "required_headers": {"x-amz-tagging": "status=temporary"}}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["post"], url_path="generate-upload-url")
def generate_upload_url(self, request):
    try:
        entity_type = (request.data.get("entity_type") or "").strip()
        content_type = (request.data.get("content_type") or "").strip()
        label = (request.data.get("label") or "").strip()

        # Validate entity_type
        if entity_type not in ALLOWED_ENTITY_TYPES:
            return self.validation_error_response(
                errors={"entity_type": [f"Must be one of: {', '.join(sorted(ALLOWED_ENTITY_TYPES))}."]},
                message="Invalid entity type."
            )

        # Validate content_type
        if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            return self.validation_error_response(
                errors={"content_type": ["Unsupported type. Allowed: image/jpeg, image/jpg, image/png, image/webp."]},
                message="Unsupported file type."
            )

        ext = _EXT_MAP.get(content_type, ".jpg")

        shop_id = get_active_shop_id(request)

        # ============ BUILD S3 KEY PER ENTITY TYPE ============
        if entity_type == "product":
            shop_service_id = request.data.get("shop_service_id")
            if not shop_service_id:
                return self.validation_error_response(
                    errors={"shop_service_id": ["Required when entity_type is 'product'."]},
                    message="shop_service_id is required for product uploads."
                )
            try:
                shop_service = ShopOwnerService.objects.select_related("general_service", "shop").get(
                    id=shop_service_id,
                    deleted__isnull=True,
                )
            except ShopOwnerService.DoesNotExist:
                return self.not_found_response(
                    resource="Service",
                    message="The selected service is not available."
                )
            shop = shop_service.shop
            shop_name = shop.s3_folder
            service_name_seg = _sanitize_s3_segment(shop_service.general_service.name or "general")
            label_seg = _sanitize_s3_segment(label or "product")
            key = f"{shop_name}/product_images/{service_name_seg}/{label_seg}_{uuid.uuid4().hex}{ext}"

        elif entity_type == "booking_document":
            from shop.models import Shop
            shop_id_param = request.data.get("shop_id") or shop_id
            try:
                shop = Shop.objects.get(id=shop_id_param)
            except Shop.DoesNotExist:
                return self.not_found_response(resource="Shop", message="Shop not found.")
            shop_name = shop.s3_folder
            user_id = request.user.id
            date_str = timezone.now().strftime("%Y%m%d")
            uid = uuid.uuid4().hex
            key = f"{shop_name}/booking_documents/user_{user_id}/{date_str}/{uid}{ext}"

        elif entity_type == "booking_thumbnail":
            from shop.models import Shop
            shop_id_param = request.data.get("shop_id") or shop_id
            try:
                shop = Shop.objects.get(id=shop_id_param)
            except Shop.DoesNotExist:
                return self.not_found_response(resource="Shop", message="Shop not found.")
            shop_name = shop.s3_folder
            product_id = request.data.get("product_id")
            if not product_id:
                return self.validation_error_response(
                    errors={"product_id": ["Required when entity_type is 'booking_thumbnail'."]},
                    message="product_id is required for booking thumbnail uploads."
                )
            version = uuid.uuid4().hex[:16]
            key = f"{shop_name}/booking_thumbnails/{shop_id_param}/{product_id}_{version}{ext}"

        # ============ GENERATE PRE-SIGNED PUT URL ============
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        client = S3Service.get_client()

        # Signed only when enabled: the PUT must then send the same header.
        cache_control = presigned_image_cache_control()
        params = {
            "Bucket": bucket,
            "Key": key,
            "Tagging": "status=temporary",
        }
        required_headers = {"x-amz-tagging": "status=temporary"}
        if cache_control:
            params["CacheControl"] = cache_control
            required_headers["Cache-Control"] = cache_control

        upload_url = client.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=PRESIGNED_URL_EXPIRY,
        )

        file_url = f"https://{bucket}.s3.amazonaws.com/{key}"

        logger.info(
            f"[Generate Upload URL] entity_type={entity_type} key={key} user_id={request.user.id}"
        )

        return self.success_response(
            data={
                "upload_url": upload_url,
                "file_url": file_url,
                # Frontend must include these headers exactly in the PUT request.
                # x-amz-tagging is required because Tagging is part of the signed URL;
                # Cache-Control too when PRESIGNED_IMAGE_CACHE_CONTROL_ENABLED is on.
                "required_headers": required_headers,
            },
            message="Upload URL generated successfully."
        )

    except Exception as e:
        logger.error(f"[Generate Upload URL] Unexpected error: {str(e)}", exc_info=True)
        return self.error_response(message="Could not generate upload URL. Please try again.")
