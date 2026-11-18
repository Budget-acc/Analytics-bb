from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework import status
from ERP.utils.custom_pagination import StandardPageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from authentication.permissions import HasMarketplaceCatalogAccess
from rest_framework.response import Response
from rest_framework.views import APIView
import json

from rest_framework_simplejwt.authentication import JWTAuthentication
from ERP.utils.mixins import StandardResponseMixin

from marketplace.api.admin_v1.serializers.subcategory import (
    MarketplaceSubCategorySerializer
)

from marketplace.models.marketplace_product import MarketplaceProduct

from marketplace.models.marketplace_subcategory import MarketplaceSubCategory

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)



class MarketplaceSubCategoryListCreateAPIView(StandardResponseMixin, APIView):
    """
    Handles GET (list with search, filtering & pagination)
    and POST (create) for SubCategories.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [HasMarketplaceCatalogAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="List marketplace subcategories",
        description=(
            "Returns a paginated list of marketplace subcategories. "
            "Supports searching by subcategory name, description, "
            "or category name. Results can also be filtered by "
            "active status and category."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Search by subcategory name, description, "
                    "or category name."
                ),
            ),
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter subcategories by active status. "
                    "Accepts true/false or 1/0."
                ),
            ),
            OpenApiParameter(
                name="category",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter subcategories by category ID.",
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
                description=(
                    "Number of records per page. Maximum 100."
                ),
            ),
            OpenApiParameter(
                name="category_names",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter subcategories by multiple category names. "
                    "Pass the parameter multiple times; matching is case-insensitive."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Subcategories retrieved successfully."
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
        },
    )

    def get(self, request, *args, **kwargs):
        """
        List subcategories with optional:

        - search
        - is_active filtering
        - category filtering
        - pagination
        """

        search = request.query_params.get("search", "").strip()
        is_active = request.query_params.get("is_active", None)
        category_id = request.query_params.get("category", None)

        queryset = (
            MarketplaceSubCategory.objects
            .select_related("category")
            .all()
            .order_by("-id")
        )

        # Active status filtering
        if is_active is not None:
            if is_active.lower() in ["true", "1"]:
                queryset = queryset.filter(is_active=True)

            elif is_active.lower() in ["false", "0"]:
                queryset = queryset.filter(is_active=False)

        # Category filtering
        if category_id:
            queryset = queryset.filter(category_id=category_id)



        category_names_param = request.query_params.get("category_names")

        if category_names_param:
            try:
                category_names = json.loads(category_names_param)

                if not isinstance(category_names, list):
                    return Response(
                        {"error": "category_names must be a list."},
                        status=400,
                    )

                category_names = [
                    name.strip()
                    for name in category_names
                    if isinstance(name, str) and name.strip()
                ]

                if category_names:
                    queryset = queryset.filter(
                        category__name__in=category_names
                    ).distinct()

            except json.JSONDecodeError:
                return Response(
                    {"error": "category_names must be a valid JSON list."},
                    status=400,
                )

        # Search filtering
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(category__name__icontains=search)
            )

        # Pagination
        paginator = StandardPageNumberPagination()

        paginated_qs = paginator.paginate_queryset(
            queryset,
            request
        )

        serializer = MarketplaceSubCategorySerializer(
            paginated_qs,
            many=True
        )

        return paginator.get_paginated_response(
            serializer.data
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Create marketplace subcategory",
        description=(
            "Creates a new marketplace subcategory. "
            "The selected category must be active."
        ),
        request=MarketplaceSubCategorySerializer,
        responses={
            201: MarketplaceSubCategorySerializer,
            400: OpenApiResponse(
                description=(
                    "Validation error. Subcategory name must be unique "
                    "and the selected category must be active."
                )
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
        },
    )

    def post(self, request, *args, **kwargs):
        """Create a new subcategory."""

        serializer = MarketplaceSubCategorySerializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        subcategory = serializer.save()

        return self.success_response(
            data=MarketplaceSubCategorySerializer(subcategory).data,
            message="Subcategory created successfully.",
        )


class MarketplaceSubCategoryDetailAPIView(StandardResponseMixin, APIView):
    """
    Handles GET (retrieve), PATCH (partial update),
    and DELETE (destroy) for a single SubCategory.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [HasMarketplaceCatalogAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, pk):
        return get_object_or_404(
            MarketplaceSubCategory,
            pk=pk
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Retrieve marketplace subcategory",
        description=(
            "Returns details of a single marketplace subcategory."
        ),
        responses={
            200: MarketplaceSubCategorySerializer,
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
            404: OpenApiResponse(
                description="Subcategory not found."
            ),
        },
    )

    def get(self, request, pk, *args, **kwargs):

        subcategory = self.get_object(pk)

        serializer = MarketplaceSubCategorySerializer(
            subcategory
        )

        return self.success_response(
            data=serializer.data,
            message="Subcategory retrieved successfully.",
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Update marketplace subcategory",
        description=(
            "Partially updates an existing marketplace subcategory. "
            "Only fields included in the request will be updated. "
            "The selected category must be active."
        ),
        request=MarketplaceSubCategorySerializer,
        responses={
            200: MarketplaceSubCategorySerializer,
            400: OpenApiResponse(
                description=(
                    "Validation error. Subcategory name must be unique "
                    "and the selected category must be active."
                )
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
            404: OpenApiResponse(
                description="Subcategory not found."
            ),
        },
    )

    def patch(self, request, pk, *args, **kwargs):

        subcategory = self.get_object(pk)

        serializer = MarketplaceSubCategorySerializer(
            subcategory,
            data=request.data,
            partial=True
        )

        serializer.is_valid(
            raise_exception=True
        )

        updated_subcategory = serializer.save()

        return self.success_response(
            data=MarketplaceSubCategorySerializer(updated_subcategory).data,
            message="Subcategory updated successfully.",
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Delete marketplace subcategory",
        description=(
            "Soft deletes an existing marketplace subcategory."
        ),
        responses={
            200: OpenApiResponse(
                description="Subcategory deleted successfully."
            ),
            401: OpenApiResponse(
                description=(
                    "Authentication credentials were not provided "
                    "or token is invalid."
                )
            ),
            403: OpenApiResponse(
                description="Admin access required. Vendor Admin (VENDOR_ADMIN with is_staff=True) has read-only access (GET only)."
            ),
            404: OpenApiResponse(
                description="Subcategory not found."
            ),
        },
        )

    def delete(self, request, pk, *args, **kwargs):

        subcategory = self.get_object(pk)
        products_exist = MarketplaceProduct.all_objects.filter(
            subcategory=subcategory
        ).exists()

        if products_exist:
            return self.error_response(
                message=(
                    "Cannot delete this subcategory because products "
                    "are associated with it."
                )
            )

        subcategory.delete()

        return self.success_response(
            data=None,
            message="Subcategory deleted successfully.",
        )