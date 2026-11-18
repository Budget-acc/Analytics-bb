from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from ERP.utils.custom_pagination import StandardPageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser,JSONParser
from authentication.permissions import HasMarketplaceCatalogAccess
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from marketplace.api.admin_v1.serializers.fabric import FabricSerializer
from marketplace.models.fabric import Fabric
from marketplace.models.marketplace_product_fabric import MarketplaceProductFabric 
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)




class FabricListCreateAPIView(StandardResponseMixin, APIView):
    """Handles GET (list with search & pagination) and POST (create) for Fabrics."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [HasMarketplaceCatalogAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="List marketplace fabrics",
        description=(
            "Returns a paginated list of marketplace fabrics. "
            "Supports searching by fabric name and filtering "
            "by active status."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search fabrics by name.",
            ),
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter fabrics by active status. "
                    "Accepts true/false or 1/0."
                ),
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
                description="Fabrics retrieved successfully."
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
        """List fabrics with optional search filtering, active status filtering, and pagination."""
        search = request.query_params.get("search", "").strip()
        is_active = request.query_params.get("is_active", None)

        queryset = Fabric.objects.all().order_by("-id")

        # Active status filtering
        if is_active is not None:
            if is_active.lower() in ["true", "1"]:
                queryset = queryset.filter(is_active=True)
            elif is_active.lower() in ["false", "0"]:
                queryset = queryset.filter(is_active=False)

        # Search filtering
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
            )

        # Pagination
        paginator = StandardPageNumberPagination()
        paginated_qs = paginator.paginate_queryset(queryset, request)
        serializer = FabricSerializer(paginated_qs, many=True)

        return paginator.get_paginated_response(serializer.data)


    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Create marketplace fabric",
        description="Creates a new marketplace fabric.",
        request=FabricSerializer,
        responses={
            201: FabricSerializer,
            400: OpenApiResponse(
                description=(
                    "Validation error. Fabric name cannot be empty "
                    "or a fabric with the same name already exists."
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
        """Create a new fabric."""
        serializer = FabricSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fabric = serializer.save()

        return self.success_response(
            data=FabricSerializer(fabric).data,
            message="Fabric created successfully.",
        )


class FabricDetailAPIView(StandardResponseMixin, APIView):
    """Handles GET (retrieve), PATCH (partial update), and DELETE (destroy) for a single Fabric."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [HasMarketplaceCatalogAccess]
    parser_classes = [JSONParser, MultiPartParser, FormParser]


    def get_object(self, pk):
        return get_object_or_404(Fabric, pk=pk)

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Retrieve marketplace fabric",
        description="Returns details of a single marketplace fabric.",
        responses={
            200: FabricSerializer,
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
                description="Fabric not found."
            ),
        },
    )

    def get(self, request, pk, *args, **kwargs):
        fabric = self.get_object(pk)
        serializer = FabricSerializer(fabric)
        return self.success_response(
            data=serializer.data,
            message="Fabric retrieved successfully.",
        )


    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Update marketplace fabric",
        description=(
            "Partially updates an existing marketplace fabric. "
            "Only fields included in the request will be updated."
        ),
        request=FabricSerializer,
        responses={
            200: FabricSerializer,
            400: OpenApiResponse(
                description="Validation error."
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
                description="Fabric not found."
            ),
        },
    )
    def patch(self, request, pk, *args, **kwargs):
        fabric = self.get_object(pk)
        serializer = FabricSerializer(fabric, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_fabric = serializer.save()

        return self.success_response(
            data=FabricSerializer(updated_fabric).data,
            message="Fabric updated successfully.",
        )

    @extend_schema(
        tags=["Marketplace Admin V1"],
        summary="Delete marketplace fabric",
        description=(
            "Soft deletes an existing marketplace fabric."
        ),
        responses={
            200: OpenApiResponse(
                description="Fabric deleted successfully."
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
                description="Fabric not found."
            ),
        },
    )

    def delete(self, request, pk, *args, **kwargs):
        fabric = self.get_object(pk)

        relationships_exist = MarketplaceProductFabric.all_objects.filter(
            fabric=fabric
        ).exists()

        if relationships_exist:
            return self.error_response(
                message=(
                    "Cannot delete this fabric because it is associated "
                    "with one or more products."
                )
            )

        return self.success_response(
            data=None,
            message="Fabric deleted successfully.",
        )