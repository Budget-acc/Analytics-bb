from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from ERP.utils.custom_pagination import StandardPageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from authentication.permissions import HasMarketplaceCatalogAccess
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from ERP.utils.mixins import StandardResponseMixin

from marketplace.api.admin_v1.serializers.category import MarketplaceCategorySerializer
from marketplace.models.marketplace_category import MarketplaceCategory
from marketplace.models.marketplace_subcategory import MarketplaceSubCategory
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)



class MarketplaceCategoryListCreateAPIView(StandardResponseMixin, APIView):
    """Handles GET (list with search & pagination) and POST (create) for Categories."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [HasMarketplaceCatalogAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="List marketplace categories",
        description=(
            "Returns a paginated list of marketplace categories. "
            "Supports search by category name or description and filtering "
            "by active status."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search by category name or description.",
            ),
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter categories by active status. Accepts true/false or 1/0.",
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
                description="Categories retrieved successfully."
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided or token is invalid."
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        """List categories with optional search filtering, active status filtering, and pagination."""
        search = request.query_params.get("search", "").strip()
        is_active = request.query_params.get("is_active", None)

        queryset = MarketplaceCategory.objects.all().order_by("-id")

        # Active status filtering
        if is_active is not None:
            if is_active.lower() in ["true", "1"]:
                queryset = queryset.filter(is_active=True)
            elif is_active.lower() in ["false", "0"]:
                queryset = queryset.filter(is_active=False)

        # Search filtering by name or description
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search)
            )

        # Pagination
        paginator = StandardPageNumberPagination()
        paginated_qs = paginator.paginate_queryset(queryset, request)
        serializer = MarketplaceCategorySerializer(paginated_qs, many=True)

        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Create marketplace category",
        description="Creates a new marketplace category.",
        request=MarketplaceCategorySerializer,
        responses={
            201: MarketplaceCategorySerializer,
            400: OpenApiResponse(
                description=(
                    "Validation error. For example, category name already exists "
                    "or category name is empty."
                )
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided or token is invalid."
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        """Create a new category."""
        serializer = MarketplaceCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = serializer.save()

        return self.success_response(
            data=MarketplaceCategorySerializer(category).data,
            message="Category created successfully.",
        )


class MarketplaceCategoryDetailAPIView(StandardResponseMixin, APIView):
    """Handles GET (retrieve), PATCH (partial update), and DELETE (destroy) for a single Category."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [HasMarketplaceCatalogAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, pk):
        return get_object_or_404(MarketplaceCategory, pk=pk)

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Retrieve marketplace category",
        description="Returns details of a single marketplace category.",
        responses={
            200: MarketplaceCategorySerializer,
            401: OpenApiResponse(
                description="Authentication credentials were not provided or token is invalid."
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
            404: OpenApiResponse(
                description="Category not found."
            ),
        },
    )
    def get(self, request, pk, *args, **kwargs):
        category = self.get_object(pk)
        serializer = MarketplaceCategorySerializer(category)
        return self.success_response(
            data=serializer.data,
            message="Category retrieved successfully.",
        )
    
    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Update marketplace category",
        description=(
            "Partially updates an existing marketplace category. "
            "Only fields included in the request will be updated."
        ),
        request=MarketplaceCategorySerializer,
        responses={
            200: MarketplaceCategorySerializer,
            400: OpenApiResponse(
                description="Validation error."
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided or token is invalid."
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
            404: OpenApiResponse(
                description="Category not found."
            ),
        },
    )
    def patch(self, request, pk, *args, **kwargs):
        category = self.get_object(pk)
        serializer = MarketplaceCategorySerializer(category, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_category = serializer.save()

        return self.success_response(
            data=MarketplaceCategorySerializer(updated_category).data,
            message="Category updated successfully.",
        )
    

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Delete marketplace category",
        description=(
            "Soft deletes a marketplace category."
        ),
        responses={
            200: OpenApiResponse(
                description="Category deleted successfully."
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided or token is invalid."
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
            404: OpenApiResponse(
                description="Category not found."
            ),
        },
    )
    def delete(self, request, pk, *args, **kwargs):
        category = self.get_object(pk)

        subcategories_exist = MarketplaceSubCategory.all_objects.filter(
            category=category
        ).exists()

        if subcategories_exist:
            return self.error_response(
                message=(
                    "Cannot delete this category because subcategories "
                    "are associated with it."
                )
            )

        category.delete()
        category.delete()

        return self.success_response(
            data=None,
            message="Category deleted successfully.",
        )