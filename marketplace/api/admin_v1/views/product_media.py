from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.parsers import JSONParser
from authentication.permissions import HasMarketplaceAccess
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework_simplejwt.authentication import (
    JWTAuthentication,
)
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.s3_cache_control import presigned_image_cache_control

from marketplace.models.marketplace_product import (
    MarketplaceProduct,
)

from marketplace.api.admin_v1.serializers.product_media import (
    PresignedMediaRequestSerializer,
    SaveProductMediaSerializer,
)

from marketplace.utils.s3_presigned import (
    generate_media_key,
    generate_presigned_upload_url,
    delete_s3_object,
)

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)

class MarketplaceProductMediaPresignedURLAPIView(
    StandardResponseMixin,
    APIView
):

    authentication_classes = [
        JWTAuthentication,
    ]

    permission_classes = [
        HasMarketplaceAccess,
    ]

    parser_classes = [
        JSONParser,
    ]

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Generate presigned S3 upload URLs",
        description=(
            "Generates presigned S3 upload URLs for product images or videos. "
            "The actual media file must be uploaded directly to S3 using "
            "the returned upload_url. "
            "A product can have a maximum of 4 images and 1 video."
        ),
        parameters=[
            OpenApiParameter(
                name="product_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="ID of the marketplace product.",
            ),
        ],
        request=PresignedMediaRequestSerializer,
        responses={
            200: OpenApiResponse(
                description="Presigned upload URLs generated successfully."
            ),
            400: OpenApiResponse(
                description=(
                    "Validation error, image limit exceeded, "
                    "video already exists, or unsupported media type."
                )
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin or Vendor Admin access required (role ADMIN or VENDOR_ADMIN with is_staff=True)."
            ),
            404: OpenApiResponse(
                description="Product not found."
            ),
        },
    )

    def post(
        self,
        request,
        product_id,
        *args,
        **kwargs
    ):

        product = get_object_or_404(
            MarketplaceProduct,
            pk=product_id,
        )

        serializer = PresignedMediaRequestSerializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        media_type = serializer.validated_data[
            "media_type"
        ]

        files = serializer.validated_data[
            "files"
        ]

        current_images = product.images or []
        current_videos = product.videos or []

        if media_type == "image":

            remaining_slots = 4 - len(
                current_images
            )

            if len(files) > remaining_slots:

                return self.validation_error_response(
                    errors={"files": [f"This product already has {len(current_images)} images. A maximum of 4 images is allowed."]},
                    message="Image limit exceeded.",
                )

        if media_type == "video":

            if len(current_videos) >= 1:

                return self.validation_error_response(
                    errors={"files": ["A product can have a maximum of 1 video."]},
                    message="Video already exists.",
                )

        results = []

        for file in files:

            file_name = file[
                "file_name"
            ]

            content_type = file[
                "content_type"
            ]

            
            extension = file_name.rsplit(".", 1)[-1].lower()

            # Get the product's category name.
            subcategory = product.subcategory
            category = getattr(subcategory, "category", None)

            category_name = (
                getattr(category, "name", None)
                or getattr(subcategory, "name", None)
                or "uncategorized"
            )

            # Generate the S3 key.
            key = generate_media_key(
                category_name=category_name,
                product_name=product.name,
                product_code=product.product_code,
                extension="jpg" if media_type == "image" else extension,
            )
            # Images only, and only when enabled (the PUT must then send it).
            cache_control = presigned_image_cache_control() if media_type == "image" else None
            presigned_data = (
                generate_presigned_upload_url(
                    key=key,
                    content_type=content_type,
                    cache_control=cache_control,
                )
            )

            results.append(
                {
                    "file_name": file_name,
                    "content_type": content_type,
                    "media_type": media_type,
                    "upload_url":
                        presigned_data[
                            "upload_url"
                        ],
                    "file_url":
                        presigned_data[
                            "file_url"
                        ],
                    "key":
                        presigned_data[
                            "key"
                        ],
                    # Present only when Cache-Control was signed into upload_url.
                    **({"required_headers": {"Cache-Control": cache_control}}
                       if cache_control else {}),
                }
            )

        return self.success_response(
            data=results,
            message="Presigned URLs generated successfully.",
        )


class MarketplaceProductMediaAPIView(
    StandardResponseMixin,
    APIView
):

    authentication_classes = [
        JWTAuthentication,
    ]

    permission_classes = [
        HasMarketplaceAccess,
    ]

    parser_classes = [
        JSONParser,
    ]

    def get_product(
        self,
        product_id,
    ):

        return get_object_or_404(
            MarketplaceProduct,
            pk=product_id,
        )


    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Save product media metadata",
        description=(
            "Saves the metadata of a product image or video after the file "
            "has been successfully uploaded to S3 using a presigned URL. "
            "The media URL and S3 key are stored in the product."
        ),
        parameters=[
            OpenApiParameter(
                name="product_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="ID of the marketplace product.",
            ),
        ],
        request=SaveProductMediaSerializer,
        responses={
            200: OpenApiResponse(
                description="Product media metadata saved successfully."
            ),
            400: OpenApiResponse(
                description=(
                    "Validation error, image limit exceeded, "
                    "or video already exists."
                )
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin or Vendor Admin access required (role ADMIN or VENDOR_ADMIN with is_staff=True)."
            ),
            404: OpenApiResponse(
                description="Product not found."
            ),
        },
        )
    def post(
        self,
        request,
        product_id,
        *args,
        **kwargs
    ):

        product = self.get_product(
            product_id
        )

        serializer = SaveProductMediaSerializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        data = serializer.validated_data

        media_type = data[
            "media_type"
        ]

        if media_type == "image":

            images = list(
                product.images or []
            )

            if len(images) >= 4:

                return self.validation_error_response(
                    errors={"images": ["A product can have a maximum of 4 images."]},
                    message="Image limit exceeded.",
                )

            image_data = {
                "url": data["url"],
                "key": data["key"],
                "thumbnail_url":
                    data.get(
                        "thumbnail_url",
                        "",
                    ),
                "alt":
                    data.get(
                        "alt",
                        "",
                    ),
                "sort_order":
                    data.get(
                        "sort_order",
                        len(images),
                    ),
            }

            images.append(
                image_data
            )

            product.images = images

        else:

            videos = list(
                product.videos or []
            )

            if len(videos) >= 1:

                return self.validation_error_response(
                    errors={"videos": ["A product can have a maximum of 1 video."]},
                    message="Video already exists.",
                )

            video_data = {
                "url": data["url"],
                "key": data["key"],
                "thumbnail_url":
                    data.get(
                        "thumbnail_url",
                        "",
                    ),
                "title":
                    data.get(
                        "title",
                        "",
                    ),
                "provider":
                    data.get(
                        "provider",
                        "s3",
                    ),
                "sort_order":
                    data.get(
                        "sort_order",
                        0,
                    ),
            }

            product.videos = [
                video_data
            ]

        product.save(
            update_fields=[
                "images",
                "videos",
                "updated_at",
            ]
        )

        return self.success_response(
            data={
                "images": product.images,
                "videos": product.videos,
            },
            message="Product media saved successfully.",
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Delete product media",
        description=(
            "Removes product media metadata from the product using the "
            "provided media type and S3 key."
        ),
        parameters=[
            OpenApiParameter(
                name="product_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="ID of the marketplace product.",
            ),
        ],
        request=OpenApiTypes.OBJECT,
        responses={
            200: OpenApiResponse(
                description="Product media removed successfully."
            ),
            400: OpenApiResponse(
                description=(
                    "Invalid media type or media key is missing."
                )
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin or Vendor Admin access required (role ADMIN or VENDOR_ADMIN with is_staff=True)."
            ),
            404: OpenApiResponse(
                description="Product not found."
            ),
        },
    )

    def delete(
        self,
        request,
        product_id,
        *args,
        **kwargs
    ):

        product = self.get_product(
            product_id
        )

        media_type = request.data.get(
            "media_type"
        )

        key = request.data.get(
            "key"
        )

        if media_type not in [
            "image",
            "video",
        ]:

            return self.validation_error_response(
                errors={"media_type": ["media_type must be image or video."]},
                message="Invalid media type.",
            )

        if not key:

            return self.validation_error_response(
                errors={"key": ["Provide the S3 key of the media to delete."]},
                message="Media key is required.",
            )

        if media_type == "image":

            images = [
                image
                for image in (
                    product.images or []
                )
                if image.get("key") != key
            ]

            product.images = images

        else:

            videos = [
                video
                for video in (
                    product.videos or []
                )
                if video.get("key") != key
            ]

            product.videos = videos

        # Delete from S3
        if key:
            delete_s3_object(key)

        product.save(
            update_fields=[
                "images",
                "videos",
                "updated_at",
            ]
        )

        return self.success_response(
            data={
                "images": product.images,
                "videos": product.videos,
            },
            message="Product media removed successfully.",
        )