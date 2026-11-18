from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework import status
from ERP.utils.custom_pagination import StandardPageNumberPagination
from rest_framework.parsers import (
    FormParser,
    JSONParser,
    MultiPartParser,
)
from authentication.permissions import HasMarketplaceAccess
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework_simplejwt.authentication import (
    JWTAuthentication,
)
from ERP.utils.mixins import StandardResponseMixin

from marketplace.api.admin_v1.serializers.product import (
    MarketplaceProductSerializer,
)

from marketplace.models.marketplace_product import (
    MarketplaceProduct,
)

from marketplace.utils.s3_presigned import delete_s3_object

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)





class MarketplaceProductListCreateAPIView(
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
        MultiPartParser,
        FormParser,
    ]

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="List marketplace products",
        description=(
            "Returns a paginated list of marketplace products. "
            "Supports searching and filtering by active status, "
            "public status, premium status, category, and subcategory."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Search by product code, name, color, model, "
                    "description, subcategory name, or category name."
                ),
            ),
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter products by active status. "
                    "Accepts true/false or 1/0."
                ),
            ),
            OpenApiParameter(
                name="is_public",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter products by marketplace visibility. "
                    "Accepts true/false or 1/0."
                ),
            ),
            OpenApiParameter(
                name="is_premium",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter premium products. "
                    "Accepts true/false or 1/0."
                ),
            ),
            OpenApiParameter(
                name="subcategory",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter products by subcategory ID.",
            ),
            OpenApiParameter(
                name="category",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter products by category ID.",
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Page number.",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Number of records per page. Maximum 100.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Products retrieved successfully."
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
        },
    )

    def get(
        self,
        request,
        *args,
        **kwargs
    ):

        search = request.query_params.get(
            "search",
            "",
        ).strip()

        is_active = request.query_params.get(
            "is_active",
            None,
        )

        is_public = request.query_params.get(
            "is_public",
            None,
        )

        is_premium = request.query_params.get(
            "is_premium",
            None,
        )

        subcategory_id = request.query_params.get(
            "subcategory",
            None,
        )

        category_id = request.query_params.get(
            "category",
            None,
        )

        queryset = (
            MarketplaceProduct.objects
            .select_related(
                "subcategory",
                "subcategory__category",
            )
            .prefetch_related(
                "product_fabrics__fabric",
            )
            .all()
            .order_by("-id")
        )

        # Active
        if is_active is not None:

            if is_active.lower() in [
                "true",
                "1",
            ]:

                queryset = queryset.filter(
                    is_active=True,
                )

            elif is_active.lower() in [
                "false",
                "0",
            ]:

                queryset = queryset.filter(
                    is_active=False,
                )

        # Public
        if is_public is not None:

            if is_public.lower() in [
                "true",
                "1",
            ]:

                queryset = queryset.filter(
                    is_public=True,
                )

            elif is_public.lower() in [
                "false",
                "0",
            ]:

                queryset = queryset.filter(
                    is_public=False,
                )

        # Premium
        if is_premium is not None:

            if is_premium.lower() in [
                "true",
                "1",
            ]:

                queryset = queryset.filter(
                    is_premium=True,
                )

            elif is_premium.lower() in [
                "false",
                "0",
            ]:

                queryset = queryset.filter(
                    is_premium=False,
                )

        # Subcategory
        if subcategory_id:

            queryset = queryset.filter(
                subcategory_id=subcategory_id,
            )

        # Category
        if category_id:

            queryset = queryset.filter(
                subcategory__category_id=category_id,
            )

        # Search
        if search:

            queryset = queryset.filter(
                Q(product_code__icontains=search)
                |
                Q(name__icontains=search)
                |
                Q(color__icontains=search)
                |
                Q(model__icontains=search)
                |
                Q(description__icontains=search)
                |
                Q(
                    subcategory__name__icontains=search
                )
                |
                Q(
                    subcategory__category__name__icontains=search
                )
            )

        paginator = StandardPageNumberPagination()

        paginated_queryset = paginator.paginate_queryset(
            queryset,
            request,
        )

        serializer = MarketplaceProductSerializer(
            paginated_queryset,
            many=True,
        )

        return paginator.get_paginated_response(
            serializer.data,
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Create marketplace product",
        description=(
            "Creates a new marketplace product. "
            "Product code is generated automatically. "
            "Images and videos are managed separately through "
            "the Product Media APIs."
        ),
        request=MarketplaceProductSerializer,
        responses={
            201: MarketplaceProductSerializer,
            400: OpenApiResponse(
                description=(
                    "Validation error. Check product name, "
                    "subcategory, fabrics, pricing, quantities, "
                    "or other product fields."
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
        },
    )
    def post(
        self,
        request,
        *args,
        **kwargs
    ):

        serializer = MarketplaceProductSerializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        product = serializer.save()

        return self.success_response(
            data=MarketplaceProductSerializer(product).data,
            message="Product created successfully.",
        )


class MarketplaceProductDetailAPIView(
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
        MultiPartParser,
        FormParser,
    ]

    def get_object(self, pk):

        return get_object_or_404(
            MarketplaceProduct.objects
            .select_related(
                "subcategory",
                "subcategory__category",
            )
            .prefetch_related(
                "product_fabrics__fabric",
            ),
            pk=pk,
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Retrieve marketplace product",
        description="Returns details of a single marketplace product.",
        responses={
            200: MarketplaceProductSerializer,
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

    def get(
        self,
        request,
        pk,
        *args,
        **kwargs
    ):

        product = self.get_object(pk)

        serializer = MarketplaceProductSerializer(
            product,
        )

        return self.success_response(
            data=serializer.data,
            message="Product retrieved successfully.",
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Update marketplace product",
        description=(
            "Partially updates an existing marketplace product. "
            "Only fields included in the request are updated. "
            "Fabrics can be updated through this endpoint. "
            "Images and videos cannot be modified through this endpoint "
            "and must be managed using the Product Media APIs."
        ),
        request=MarketplaceProductSerializer,
        responses={
            200: MarketplaceProductSerializer,
            400: OpenApiResponse(
                description=(
                    "Validation error. Check product name, "
                    "subcategory, fabrics, pricing, quantities, "
                    "or other product fields."
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

    def patch(
        self,
        request,
        pk,
        *args,
        **kwargs
    ):

        product = self.get_object(pk)

        serializer = MarketplaceProductSerializer(
            product,
            data=request.data,
            partial=True,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        updated_product = serializer.save()

        return self.success_response(
            data=MarketplaceProductSerializer(updated_product).data,
            message="Product updated successfully.",
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Delete marketplace product",
        description=(
            "Deletes a marketplace product. "
            "The product is soft deleted and its associated "
            "S3 images and videos are removed."
        ),
        responses={
            200: OpenApiResponse(
                description=(
                    "Product and associated media deleted successfully."
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

    def delete(self,request,pk,*args,**kwargs):

        product = self.get_object(pk)
        

        # ---------------------------------------------
        # Delete images from S3
        # ---------------------------------------------

        for image in product.images or []:

            key = image.get("key")

            if key:
                delete_s3_object(key)


        # ---------------------------------------------
        # Delete videos from S3
        # ---------------------------------------------

        for video in product.videos or []:

            key = video.get("key")

            if key:
                delete_s3_object(key)

        # ---------------------------------------------
        # Soft delete product
        # ---------------------------------------------


        product.delete()

        return self.success_response(
            data=None,
            message="Product deleted successfully.",
        )