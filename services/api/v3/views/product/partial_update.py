import logging
import re
import json
import uuid
import threading
from decimal import Decimal, InvalidOperation
from django.db import transaction, IntegrityError
from django.db.models.functions import Lower
from PIL import Image
from rest_framework.exceptions import ValidationError
from services.models import Product
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from services.api.v3.serializers import ProductSerializer
from services.api.v3.views.product.constants import (
    ALLOWED_IMAGE_CONTENT_TYPES,
    MAX_IMAGE_SIZE_BYTES,
    IMAGE_PROCESSING_SENTINEL,
)
from ERP.utils.shop_activity_logs import create_activity_log
from services.api.v3.views.product.helpers.image_helpers import _sanitize_s3_segment, _dispatch_image_task, _dispatch_thumbnail_task

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Update product",
    description="Partially updates a product with validation for pricing, duplicate identity, attributes, and optional image updates.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Product id.")],
    request=inline_serializer(
        name="ServiceV3ProductPatchRequest",
        fields={
            "name": serializers.CharField(required=False),
            "description": serializers.CharField(required=False, allow_blank=True),
            "color": serializers.CharField(required=False, allow_blank=True),
            "category": serializers.CharField(required=False, allow_blank=True),
            "model": serializers.CharField(required=False, allow_blank=True),
            "purchase_price": serializers.IntegerField(required=False),
            "price": serializers.IntegerField(required=False),
            "sale_price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "hourly_rental_price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "image": serializers.ImageField(required=False),
            "image_url": serializers.URLField(required=False),
            "attributes": serializers.JSONField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Product updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation messages include:\n"
                "- Please enter a valid price for your product.\n"
                "- Price cannot be negative.\n"
                "- Purchase price cannot be negative.\n"
                "- Sale price must be a valid amount.\n"
                "- Hourly rental price must be a valid amount.\n"
                "- Unsupported image type. Allowed: JPEG, PNG, WebP.\n"
                "- Image must be less than 3MB.\n"
                "- Invalid or unsupported image format.\n"
                "- Code already exists.\n"
                "- Validation failed. Please check your input and try again."
            )
        ),
        404: OpenApiResponse(description="Exact message: The product you are trying to update was not found."),
        500: OpenApiResponse(
            description=(
                "Server/business errors include:\n"
                "- A product with the same name, color, model, and category already exists.\n"
                "- Something went wrong while updating your product. Please try again.\n"
                "- Something went wrong while updating the product. Please try again."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Blue Shirt V2", "price": 1300}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Product updated successfully.", "data": {"id": 15, "name": "Blue Shirt V2"}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found", value={"status": "not_found", "message": "The product you are trying to update was not found.", "data": None}, response_only=True, status_codes=["404"]),
    ],
)

def partial_update(self, request, pk=None):
    """
    Update product with optimized image handling:
    - Images are validated with header-only sniff (no processing in request)
    - Image processing dispatched asynchronously after DB commit
    - Prevents blocking HTTP response with S3 operations
    - Handles race conditions with atomic transactions
    - Validates against security attacks (size limits, MIME types)
    """
    # Get shop_id early for authorization check
    try:
        from shop.api.v3.utils.shop import get_active_shop_id
        shop_id = get_active_shop_id(request)
    except Exception as e:
        logger.error(f"[Product Update] Shop ID fetch error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Could not identify your shop. Please try again."
        )

    # Check product ownership in the query itself (prevents IDOR attacks)
    try:
        product = Product.objects.select_related(
            'shop_service__general_service',
            'shop_service__shop'
        ).get(
            pk=pk,
            shop_service__shop_id=shop_id,  # Authorization check in query
            deleted__isnull=True  # Don't allow updates to soft-deleted products
        )
    except Product.DoesNotExist:
        return self.not_found_response(
            resource="Product",
            message="The product you are trying to update was not found."
        )

    try:
        data = request.data.copy()

        # ============ EXTRACT ATTRIBUTES EARLY ============
        MAX_ATTRIBUTE_JSON_SIZE = 10 * 1024  # 10KB (DOS protection)
        attributes_data = None

        # Check if attributes sent as JSON string
        if 'attributes' in data:
            attributes_raw = data.pop('attributes')
            if isinstance(attributes_raw, str):
                # Validate JSON size before parsing (DOS protection)
                if len(attributes_raw) > MAX_ATTRIBUTE_JSON_SIZE:
                    return self.validation_error_response(
                        errors={"attributes": ["Attribute data too large (max 10KB)."]},
                        message="Attribute data exceeds maximum size."
                    )
                try:
                    attributes_data = json.loads(attributes_raw)
                except json.JSONDecodeError as je:
                    logger.error(f"[Product Update] JSON decode error for attributes: {str(je)}", exc_info=True)
                    return self.validation_error_response(
                        errors={"attributes": ["Invalid JSON format for attributes."]},
                        message="Attributes must be a valid JSON object."
                    )
            elif isinstance(attributes_raw, dict):
                attributes_data = attributes_raw

        # Check if attributes sent as bracket notation
        if attributes_data is None:
            attributes_data = {}
            keys_to_remove = []
            for key in list(data.keys()):  # Use list() to avoid dict size change during iteration
                if key.startswith('attributes[') and key.endswith(']'):
                    attr_key = key[11:-1]
                    # Sanitize attribute key (prevent injection attacks)
                    if len(attr_key) > 100 or not attr_key.replace('_', '').replace('-', '').isalnum():
                        continue
                    attributes_data[attr_key] = data[key]
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                data.pop(key)

            if not attributes_data:
                attributes_data = None

        # New flow: frontend uploads directly to S3 via pre-signed URL and passes the final URL.
        # Existing flow: file upload through this endpoint — behaviour unchanged.
        presigned_image_url = (request.data.get("image_url") or "").strip() or None
        image_file = request.FILES.get("image")
        raw_image_bytes = None
        filename = None
        thumb_filename = None

        if presigned_image_url:
            # New pre-signed URL flow — image already in S3, generate thumb path only.
            from django.conf import settings as _settings
            _bucket = _settings.AWS_STORAGE_BUCKET_NAME
            _expected_prefix = f"https://{_bucket}.s3.amazonaws.com/"
            if not presigned_image_url.startswith(_expected_prefix):
                return self.validation_error_response(
                    errors={"image_url": ["Invalid image URL."]},
                    message="The image URL provided is not valid."
                )
            shop_name_seg = product.shop_service.shop.s3_folder
            service_name_seg_t = _sanitize_s3_segment(product.shop_service.general_service.name or "general")
            product_name_seg_t = _sanitize_s3_segment(product.name or f"product_{uuid.uuid4()}")
            thumb_filename = (
                f"{shop_name_seg}/product_images/{service_name_seg_t}/"
                f"{product_name_seg_t}_thumb_{uuid.uuid4().hex}.jpg"
            )

        elif image_file:
            if image_file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
                return self.validation_error_response(
                    errors={"image": ["Unsupported image type. Allowed: JPEG, PNG, WebP."]},
                    message="Please upload a JPEG, PNG, or WebP image."
                )

            if image_file.size > MAX_IMAGE_SIZE_BYTES:
                return self.validation_error_response(
                    errors={"image": ["Image must be less than 3MB."]},
                    message="Please upload an image smaller than 3MB."
                )

            # Header-only format sniff — reads just enough bytes to detect format.
            # Do NOT call .load(), .verify(), or access .size/.pixels here.
            try:
                img_sniff = Image.open(image_file)
                _ = img_sniff.format  # triggers header read only
            except Exception:
                return self.validation_error_response(
                    errors={"image": ["Invalid or unsupported image format."]},
                    message="Please upload a valid image file."
                )

            image_file.seek(0)
            raw_image_bytes = image_file.read()

            shop_name = product.shop_service.shop.s3_folder
            service_name_seg = _sanitize_s3_segment(product.shop_service.general_service.name or "general")
            product_name_seg = _sanitize_s3_segment(product.name or f"product_{uuid.uuid4()}")

            filename = f"{shop_name}/product_images/{service_name_seg}/{product_name_seg}_{uuid.uuid4().hex}.jpg"
            thumb_filename = f"{shop_name}/product_images/{service_name_seg}/{product_name_seg}_thumb_{uuid.uuid4().hex}.jpg"

        # ============ VALIDATE PRICES ============
        try:
            # Safe price validation (only validate if provided in request)
            if 'price' in data:
                price_raw = data.get('price')
                if price_raw not in [None, "", "null"]:
                    price = int(price_raw)
                    if price < 0:
                        return self.validation_error_response(
                            errors={"price": ["Price cannot be negative."]},
                            message="Please enter a valid price for your product."
                        )

            if 'purchase_price' in data:
                purchase_price_raw = data.get('purchase_price')
                if purchase_price_raw not in [None, "", "null"]:
                    purchase_price = int(purchase_price_raw)
                    if purchase_price < 0:
                        return self.validation_error_response(
                            errors={"purchase_price": ["Purchase price cannot be negative."]},
                            message="Please enter a valid purchase price."
                        )

            # Validate sale_price and hourly_rental_price
            if 'sale_price' in data:
                sale_price_raw = data.get('sale_price')
                if sale_price_raw not in [None, "", "null"]:
                    try:
                        sale_price = Decimal(str(sale_price_raw))
                        if sale_price < 0:
                            return self.validation_error_response(
                                errors={"sale_price": ["Sale price cannot be negative."]},
                                message="Please enter a valid sale price."
                            )
                        data['sale_price'] = sale_price
                    except (InvalidOperation, ValueError):
                        return self.validation_error_response(
                            errors={"sale_price": ["Sale price must be a valid amount."]},
                            message="Please enter a valid sale price.",
                            dev_message="Invalid Decimal conversion for sale_price"
                        )

            if 'hourly_rental_price' in data:
                hourly_rental_price_raw = data.get('hourly_rental_price')
                if hourly_rental_price_raw not in [None, "", "null"]:
                    try:
                        hourly_rental_price = Decimal(str(hourly_rental_price_raw))
                        if hourly_rental_price < 0:
                            return self.validation_error_response(
                                errors={"hourly_rental_price": ["Hourly rental price cannot be negative."]},
                                message="Please enter a valid hourly rental price."
                            )
                        data['hourly_rental_price'] = hourly_rental_price
                    except (InvalidOperation, ValueError):
                        return self.validation_error_response(
                            errors={"hourly_rental_price": ["Hourly rental price must be a valid amount."]},
                            message="Please enter a valid hourly rental price.",
                            dev_message="Invalid Decimal conversion for hourly_rental_price"
                        )

        except ValueError as ve:
            # Handle integer conversion errors for price/purchase_price
            return self.validation_error_response(
                errors={"price": ["Price must be a valid number."]},
                message="Please enter a valid price for your product.",
                dev_message=f"Invalid integer value: {str(ve)}"
            )

        # ============ VALIDATE DATA ============
        serializer = ProductSerializer(product, data=data, partial=True)
        if not serializer.is_valid():
            error_meta = self._get_serializer_error_metadata(serializer.errors)
            logger.error(
                f"[Product Update] Serializer validation failed for product {pk}",
                extra={
                    "product_id": pk,
                    "shop_id": shop_id,
                    "error_fields": error_meta["error_fields"],
                    "error_count": error_meta["error_count"],
                },
            )

            user_message = self._build_serializer_message(serializer.errors)

            return self.validation_error_response(
                errors=serializer.errors,
                message=user_message,
                dev_message=(
                    f"Product update validation failed. "
                    f"Error fields: {', '.join(error_meta['error_fields']) if error_meta['error_fields'] else 'none'} "
                    f"(count={error_meta['error_count']})"
                )
            )

        # ============ DUPLICATE CHECK (case-insensitive) ============
        validated = serializer.validated_data
        duplicate_exists = Product.objects.annotate(
            name_l=Lower("name"),
            color_l=Lower("color"),
            model_l=Lower("model"),
            category_l=Lower("category"),
        ).filter(
            shop_service=product.shop_service,
            name_l=validated.get("name", product.name).lower(),
            color_l=validated.get("color", product.color).lower(),
            model_l=validated.get("model", product.model).lower(),
            category_l=validated.get("category", product.category).lower(),
            deleted__isnull=True
        ).exclude(pk=product.pk).exists()

        if duplicate_exists:
            return self.error_response(
                message="A product with the same name, color, model, and category already exists."
            )

        # ============ ATOMIC UPDATE (prevents race conditions) ============
        with transaction.atomic():
            # Lock the product row to prevent concurrent updates
            product = Product.objects.select_for_update().get(pk=product.pk)

            # ============ VALIDATE SERVICE ATTRIBUTES ============
            if attributes_data is not None:
                general_service = product.shop_service.general_service
                logger.info(f"[Product Update] Validating attributes for product {pk}: {attributes_data}")

                attr_valid, attr_errors, validated_attributes = self._validate_and_save_product_attributes(
                    general_service=general_service,
                    attributes_data=attributes_data,
                    product=product
                )

                if not attr_valid:
                    logger.error(
                        f"[Product Update] Attribute validation failed for product {pk}. Errors: {attr_errors}",
                        extra={
                            'product_id': pk,
                            'shop_id': shop_id,
                            'service_id': general_service.id,
                            'attributes_data': attributes_data,
                            'validation_errors': attr_errors
                        }
                    )

                    # Flatten attribute errors for better UX
                    flattened_errors = {}
                    error_messages = []

                    for field, errors in attr_errors.items():
                        field_display = field.replace('_', ' ').title()

                        if isinstance(errors, list):
                            for error in errors:
                                error_messages.append(f"{field_display}: {error}")
                                flattened_errors[field] = errors
                        else:
                            error_messages.append(f"{field_display}: {errors}")
                            flattened_errors[field] = [errors]

                    combined_message = " | ".join(error_messages) if error_messages else "Please check the service attributes and try again."

                    return self.validation_error_response(
                        errors=flattened_errors,
                        message=combined_message
                    )

                logger.info(f"[Product Update] Attributes validated successfully for product {pk}. Count: {len(validated_attributes)}")
            else:
                validated_attributes = None

            # ============ REGISTRATION NUMBER UNIQUENESS CHECK ============
            if validated_attributes:
                self._validate_registration_number_uniqueness(
                    shop_id, validated_attributes, product=product
                )

            # ============ SAVE PRODUCT ============
            if presigned_image_url is not None:
                # New flow: main image already in S3, set it directly; thumbnail pending.
                updated_product = serializer.save(
                    image=presigned_image_url,
                    thumbnail_image=IMAGE_PROCESSING_SENTINEL
                )
            elif raw_image_bytes is not None:
                # Existing flow: use sentinel while Celery processes the upload.
                updated_product = serializer.save(
                    image=IMAGE_PROCESSING_SENTINEL,
                    thumbnail_image=IMAGE_PROCESSING_SENTINEL
                )
            else:
                # No new image, keep existing images
                updated_product = serializer.save()

            # ============ SAVE SERVICE ATTRIBUTES ============
            if validated_attributes is not None:
                logger.info(f"[Product Update] Saving {len(validated_attributes)} attributes for product {pk}")
                self._save_product_attributes(updated_product, validated_attributes, is_partial_update=True)
                logger.info(f"[Product Update] Attributes saved successfully for product {pk}")

            # ============ DISPATCH IMAGE TASK AFTER DB COMMIT ============
            # Dispatch after transaction commits to ensure product row is visible to other connections
            if raw_image_bytes is not None:
                # Existing flow: full encode + upload via Celery.
                _product_id = updated_product.id
                _raw = raw_image_bytes
                _fname = filename
                _thumb = thumb_filename
                transaction.on_commit(
                    lambda: _dispatch_image_task(_product_id, _raw, _fname, _thumb)
                )
            elif presigned_image_url is not None:
                # New pre-signed URL flow: main image already in S3, generate thumbnail only.
                # _dispatch_thumbnail_task runs in a daemon thread so broker failures
                # never crash the HTTP response.
                _product_id = updated_product.id
                _img_url = presigned_image_url
                _thumb = thumb_filename
                transaction.on_commit(
                    lambda: _dispatch_thumbnail_task(_product_id, _img_url, _thumb)
                )

        return self.success_response(
            data=ProductSerializer(updated_product).data,
            message="Product updated successfully."
        )

    except ValidationError as ve:
        return self._handle_product_validation_error(ve, context="Product Update")

    except IntegrityError as ie:
        if 'unique_external_qr_per_shop' in str(ie):
            return self.validation_error_response(
                errors={"external_qr_code": ["Code already exists."]},
                message="Code already exists."
            )
        cause = getattr(ie, '__cause__', None)
        pgcode = getattr(cause, 'pgcode', '') or ''
        is_unique_violation = (
            pgcode == '23505'
            or 'unique constraint' in str(ie).lower()
            or 'duplicate key' in str(ie).lower()
        )
        if is_unique_violation:
            return self.error_response(
                message="A product with the same name, color, model, and category already exists."
            )
        else:
            return self.error_response(
                message="Something went wrong while updating your product. Please try again."
            )

    except Exception as e:
        logger.error(
            f"[Product Update] Unexpected error for product {pk}: {str(e)}",
            exc_info=True,
            extra={
                'product_id': pk,
                'shop_id': shop_id if 'shop_id' in locals() else None,
                'request_data': request.data if hasattr(request, 'data') else None
            }
        )
        return self.error_response(
            message="Something went wrong while updating the product. Please try again."
        )
    finally:
        # No cleanup needed - we don't process images inline anymore
        pass
