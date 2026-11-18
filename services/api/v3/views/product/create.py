import re
import uuid
import json
import logging
from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError
from rest_framework import serializers
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from django.db import IntegrityError, transaction
from PIL import Image

from services.models import Product, ProductVariant, ShopOwnerService
from services.api.v3.serializers import ProductSerializer
from services.api.v3.views.product.constants import (
    ALLOWED_IMAGE_CONTENT_TYPES,
    MAX_IMAGE_SIZE_BYTES,
    IMAGE_PROCESSING_SENTINEL,
)
from services.api.v3.views.product.helpers.image_helpers import _sanitize_s3_segment, _dispatch_image_task, _dispatch_thumbnail_task
from shop.api.v3.utils.shop import get_active_shop_id

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Create product",
    description="Creates a product with variants, optional attributes, optional image upload/presigned URL, and uniqueness validations.",
    request=inline_serializer(
        name="ServiceV3ProductCreateRequest",
        fields={
            "shop_service_id": serializers.IntegerField(required=True, help_text="Target shop service id."),
            "name": serializers.CharField(required=True),
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
        200: OpenApiResponse(description="Exact message: Product created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation messages include:\n"
                "- Please select a service for this product.\n"
                "- The selected service is not available.\n"
                "- Price must be a valid number.\n"
                "- Sale price must be a valid amount.\n"
                "- Hourly rental price must be a valid amount.\n"
                "- Unsupported image type. Allowed: JPEG, PNG, WebP.\n"
                "- Image must be less than 3MB.\n"
                "- Invalid or unsupported image format.\n"
                "- Invalid JSON format for attributes.\n"
                "- Only one variant can have an empty attribute.\n"
                "- Code already exists.\n"
                "- Validation failed. Please check your input and try again."
            )
        ),
        500: OpenApiResponse(
            description=(
                "Server/business errors include:\n"
                "- A product with the same name, color, model, and category already exists.\n"
                "- Product limit reached ({effective_limit}). Please upgrade your plan.\n"
                "- Something went wrong while saving your product. Please try again.\n"
                "- Something went wrong while creating the product. Please try again."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_service_id": 8, "name": "Blue Shirt", "price": 1200, "purchase_price": 800, "variants[0][attribute]": "M", "variants[0][stock]": 10}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Product created successfully.", "data": {"id": 15, "image_processing": True}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Duplicate response", value={"status": "error", "message": "A product with the same name, color, model, and category already exists.", "data": None}, response_only=True, status_codes=["500"]),
    ],
)
def create(self, request):
    try:
        shop_id = get_active_shop_id(request)
        data = request.data.dict() if hasattr(request.data, 'dict') else dict(request.data)

        # ============ EXTRACT ATTRIBUTES EARLY ============
        MAX_ATTRIBUTE_JSON_SIZE = 10 * 1024  # 10KB
        attributes_data = None
        if 'attributes' in data:
            attributes_raw = data.pop('attributes')
            if isinstance(attributes_raw, str):
                if len(attributes_raw) > MAX_ATTRIBUTE_JSON_SIZE:
                    return self.validation_error_response(
                        errors={"attributes": ["Attribute data too large (max 10KB)."]},
                        message="Attribute data exceeds maximum size."
                    )
                try:
                    attributes_data = json.loads(attributes_raw)
                except json.JSONDecodeError as je:
                    logger.error(f"[Product Create] JSON decode error for attributes: {str(je)}", exc_info=True)
                    return self.validation_error_response(
                        errors={"attributes": ["Invalid JSON format for attributes."]},
                        message="Attributes must be a valid JSON object."
                    )
            elif isinstance(attributes_raw, dict):
                attributes_data = attributes_raw

        if attributes_data is None:
            attributes_data = {}
            keys_to_remove = []
            for key in data.keys():
                if key.startswith('attributes[') and key.endswith(']'):
                    attr_key = key[11:-1]
                    if not attr_key or len(attr_key) > 100:
                        continue
                    if not re.sub(r'[-_]', '', attr_key).isalnum():
                        continue
                    attributes_data[attr_key] = data[key]
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                data.pop(key)
            if not attributes_data:
                attributes_data = None

        # ============ PARSE VARIANTS ============
        variants_data = []
        i = 0
        empty_attr_count = 0
        while True:
            attr_key = f'variants[{i}][attribute]'
            stock_key = f'variants[{i}][stock]'
            length_key = f'variants[{i}][fabric_length]'
            qr_key = f'variants[{i}][external_qr_code]'
            if not any(k in data for k in [attr_key, stock_key, length_key, qr_key]):
                break

            attribute_val = data.get(attr_key, "").strip()
            stock_val = data.get(stock_key)
            fabric_length_val = data.get(length_key)
            external_qr_code_val = data.get(qr_key, None)

            if attribute_val == "":
                empty_attr_count += 1

            if stock_val not in [None, "", "null"]:
                try:
                    stock_val = int(stock_val)
                    if stock_val < 0:
                        return self.validation_error_response(
                            errors={stock_key: ["Stock cannot be negative."]},
                            message="Please enter a valid stock quantity."
                        )
                except ValueError:
                    return self.validation_error_response(
                        errors={stock_key: ["Stock must be a number."]},
                        message="Please enter a valid stock quantity."
                    )
            else:
                stock_val = None

            if fabric_length_val not in [None, "", "null"]:
                try:
                    fabric_length_val = float(fabric_length_val)
                    if fabric_length_val < 0:
                        return self.validation_error_response(
                            errors={length_key: ["Fabric length cannot be negative."]},
                            message="Please enter a valid fabric length."
                        )
                except ValueError:
                    return self.validation_error_response(
                        errors={length_key: ["Fabric length must be a number."]},
                        message="Please enter a valid fabric length."
                    )
            else:
                fabric_length_val = None

            if stock_val is None and fabric_length_val is None:
                return self.validation_error_response(
                    errors={attr_key: ["Either stock or fabric_length is required."]},
                    message=f"Variant '{attribute_val or 'Unnamed'}' must have stock or fabric length."
                )

            variants_data.append({
                "attribute": attribute_val,
                "stock": stock_val,
                "fabric_length": fabric_length_val,
                "external_qr_code": external_qr_code_val
            })
            i += 1

        if len(variants_data) > 1 and empty_attr_count > 1:
            return self.validation_error_response(
                errors={"variants": ["Only one variant can have an empty attribute."]},
                message="Only one variant can have an empty attribute."
            )

        for key in list(data.keys()):
            if key.startswith("variants["):
                data.pop(key)

        # ============ SHOP SERVICE FETCH ============
        shop_service_id = data.get("shop_service_id")
        if not shop_service_id:
            return self.validation_error_response(
                errors={"shop_service_id": ["This field is required."]},
                message="Please select a service for this product."
            )

        try:
            shop_service = ShopOwnerService.objects.select_related("general_service", "shop").get(
                id=shop_service_id,
                shop_id=shop_id,          # Ensures the service belongs to the authenticated shop
                deleted__isnull=True,
            )
            general_service = shop_service.general_service
            shop = shop_service.shop
        except ShopOwnerService.DoesNotExist:
            return self.not_found_response(
                resource="Service",
                message="The selected service is not available."
            )

        # ============ PRICE PARSING ============
        try:
            price_raw = data.get("price", "0")
            purchase_price_raw = data.get("purchase_price", "0")
            sale_price_raw = data.get("sale_price")
            hourly_rental_price_raw = data.get("hourly_rental_price")

            price = int(price_raw)
            purchase_price = int(purchase_price_raw)

            if sale_price_raw in [None, "", "null"]:
                sale_price = None
            else:
                sale_price = Decimal(str(sale_price_raw))
                if sale_price < 0:
                    raise InvalidOperation

            if hourly_rental_price_raw in [None, "", "null"]:
                hourly_rental_price = None
            else:
                hourly_rental_price = Decimal(str(hourly_rental_price_raw))
                if hourly_rental_price < 0:
                    raise InvalidOperation

        except ValueError:
            return self.validation_error_response(
                errors={"price": ["Price must be a valid number."]},
                message="Please enter a valid price for your product.",
                dev_message="Invalid integer value for price or purchase_price"
            )

        except (InvalidOperation, TypeError) as e:
            error_field = "sale_price"
            if hourly_rental_price_raw not in [None, "", "null"]:
                try:
                    Decimal(str(sale_price_raw))
                    error_field = "hourly_rental_price"
                except (InvalidOperation, ValueError, TypeError):
                    pass

            error_messages = {
                "sale_price": ("Sale price must be a valid amount.", "Invalid Decimal conversion for sale_price"),
                "hourly_rental_price": ("Hourly rental price must be a valid amount.", "Invalid Decimal conversion for hourly_rental_price")
            }
            user_msg, dev_msg = error_messages.get(error_field, error_messages["sale_price"])
            return self.validation_error_response(
                errors={error_field: [user_msg]},
                message=user_msg,
                dev_message=dev_msg
            )

        # ============ SERIALIZER VALIDATION ============
        serializer_data = {
            "shop_service_id": shop_service_id,
            "name": data.get("name"),
            "purchase_price": purchase_price,
            "price": price,
            "sale_price": sale_price,
            "hourly_rental_price": hourly_rental_price,
            "description": data.get("description"),
            "color": data.get("color"),
            "category": data.get("category"),
            "model": data.get("model"),
            "variants": variants_data,
        }

        serializer = ProductSerializer(data=serializer_data)

        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError as ve:
            error_meta = self._get_serializer_error_metadata(serializer.errors)
            logger.error(
                "[Product Create] ProductSerializer validation failed",
                extra={
                    "error_fields": error_meta["error_fields"],
                    "error_count": error_meta["error_count"],
                    "exception_detail": ve.detail,
                    "shop_id": shop_id,
                },
                exc_info=True
            )

            error_dict = ve.detail
            if "variants" in error_dict:
                error_msg = error_dict["variants"][0]
                if "Duplicate variant attributes" in str(error_msg):
                    return self.validation_error_response(
                        errors={"variants": [error_msg]},
                        message=str(error_msg)
                    )

            user_message = self._build_serializer_message(serializer.errors)
            return self.validation_error_response(
                errors=serializer.errors,
                message=user_message,
                dev_message=(
                    f"Product create validation failed. "
                    f"Error fields: {', '.join(error_meta['error_fields']) if error_meta['error_fields'] else 'none'} "
                    f"(count={error_meta['error_count']})"
                )
            )

        # ============ ATTRIBUTE VALIDATION ============
        attr_valid, attr_errors, validated_attributes = self._validate_and_save_product_attributes(
            general_service=general_service,
            attributes_data=attributes_data
        )

        if not attr_valid:
            logger.error(
                f"[Product Create] Attribute validation failed. Errors: {attr_errors}",
                extra={
                    'shop_id': shop_id,
                    'service_id': general_service.id,
                    'attributes_data': attributes_data,
                    'validation_errors': attr_errors
                }
            )
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
            return self.validation_error_response(errors=flattened_errors, message=combined_message)

        # ============ REGISTRATION NUMBER UNIQUENESS CHECK ============
        self._validate_registration_number_uniqueness(shop_id, validated_attributes or [])

        # ============ PRODUCT LIMIT CHECK — before image work (fail fast) ============
        total_products = Product.objects.filter(
            shop_service__shop_id=shop.id,
            deleted__isnull=True
        ).count()
        from services.api.v3.views.product.constants import PRODUCT_PLAN_DEFAULT_LIMIT
        effective_limit = PRODUCT_PLAN_DEFAULT_LIMIT + shop.extra_stock_limit
        if total_products >= effective_limit:
            return self.error_response(
                message=f"Product limit reached ({effective_limit}). Please upgrade your plan."
            )

        # ============ IMAGE HANDLING ============
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
            shop_name_seg = shop.s3_folder
            service_name_seg_t = _sanitize_s3_segment(general_service.name or "general")
            product_name_seg_t = _sanitize_s3_segment(data.get("name") or f"product_{uuid.uuid4()}")
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

            shop_name = shop.s3_folder
            service_name_seg = _sanitize_s3_segment(general_service.name or "general")
            product_name_seg = _sanitize_s3_segment(data.get("name") or f"product_{uuid.uuid4()}")

            filename = f"{shop_name}/product_images/{service_name_seg}/{product_name_seg}_{uuid.uuid4().hex}.jpg"
            thumb_filename = f"{shop_name}/product_images/{service_name_seg}/{product_name_seg}_thumb_{uuid.uuid4().hex}.jpg"

        # ============ TRANSACTION ============
        try:
            with transaction.atomic():
                self._validate_external_qr_codes(shop, variants_data)

                product_data = serializer.validated_data
                product = Product.objects.create(
                    shop_service=shop_service,
                    name=product_data.get("name", ""),
                    description=product_data.get("description", ""),
                    color=product_data.get("color", ""),
                    category=product_data.get("category", ""),
                    model=product_data.get("model", ""),
                    price=price,
                    purchase_price=purchase_price,
                    sale_price=sale_price,
                    hourly_rental_price=hourly_rental_price,
                    image=presigned_image_url if presigned_image_url else (IMAGE_PROCESSING_SENTINEL if raw_image_bytes else ""),
                    thumbnail_image=IMAGE_PROCESSING_SENTINEL if (presigned_image_url or raw_image_bytes) else "",
                )

                created_variants = []
                variants_to_create = [
                    ProductVariant(
                        product=product,
                        shop=shop,
                        attribute=variant_data.get("attribute", "").strip(),
                        stock=int(variant_data["stock"]) if variant_data.get("stock") is not None else 0,
                        fabric_length=variant_data.get("fabric_length"),
                        external_qr_code=(variant_data.get("external_qr_code") or None),
                    )
                    for variant_data in variants_data
                ]

                if variants_to_create:
                    created_variants = ProductVariant.objects.bulk_create(variants_to_create)

                    for variant in created_variants:
                        variant.unique_code = (
                            f"S{product.shop_service.id}-P{product.id}-V{variant.id}"
                        )

                    ProductVariant.objects.bulk_update(created_variants, ["unique_code"])

                if validated_attributes:
                    self._save_product_attributes(product, validated_attributes, is_partial_update=False, is_new=True)

                # Dispatch image task AFTER DB commit — prevents Celery worker from
                # fetching the product row before it is visible to other connections.
                if raw_image_bytes is not None:
                    # Existing flow: full encode + upload via Celery.
                    _product_id = product.id
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
                    _product_id = product.id
                    _img_url = presigned_image_url
                    _thumb = thumb_filename
                    transaction.on_commit(
                        lambda: _dispatch_thumbnail_task(_product_id, _img_url, _thumb)
                    )

        except ValidationError:
            raise  # re-raise so outer handler catches it

        response_data = {"id": product.id}
        if raw_image_bytes is not None:
            response_data["image_processing"] = True
        elif presigned_image_url is not None:
            response_data["thumbnail_processing"] = True

        return self.success_response(
            data=response_data,
            message="Product created successfully."
        )

    except ValidationError as ve:
        return self._handle_product_validation_error(ve, context="Product Create")

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
                message="Something went wrong while saving your product. Please try again."
            )

    except Exception as e:
        logger.error(
            f"[Product Create] Unexpected error: {str(e)}",
            exc_info=True,
            extra={
                'shop_id': shop_id if 'shop_id' in locals() else None,
                'request_data': request.data if hasattr(request, 'data') else None
            }
        )
        return self.error_response(
            message="Something went wrong while creating the product. Please try again."
        )
