from django.db.models import (
    Q,
    Prefetch,
    Min,
    Max,
    OuterRef,
    Exists,
    F,
)
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.mixins import StandardResponseMixin

from marketplace.models.marketplace_product import MarketplaceProduct
from marketplace.models.marketplace_product_fabric import MarketplaceProductFabric

from marketplace.api.v3.serializers.product import (
    MarketplaceProductListSerializer,
    MarketplaceProductDetailSerializer,
    ProductFilterSerializer,
)

from drf_spectacular.utils import (
    extend_schema,
    OpenApiResponse,
    OpenApiExample,
)
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from rest_framework.permissions import IsAuthenticated
from django.db.models import Subquery

class MarketplaceProductViewSet(StandardResponseMixin, viewsets.ReadOnlyModelViewSet):
    """
    Client-facing marketplace product ViewSet.
    
    Provides:
    - POST /products/filter/ - List products with POST-based filtering
    - GET /products/{id}/ - Retrieve single product details
    
    All endpoints return only public, active products.
    Purchase costs are hidden, selling prices shown only if show_selling_price=True.
    """
    
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPageNumberPagination
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return MarketplaceProductDetailSerializer
        return MarketplaceProductListSerializer
    
    def get_queryset(self):
        """
        Base queryset: only public, active products.
        Optimized with prefetch_related to avoid N+1 queries.
        """
        return MarketplaceProduct.objects.filter(
            is_public=True,
            is_active=True,
            subcategory__is_active=True,
            subcategory__category__is_active=True,
        ).select_related(
            'subcategory',
            'subcategory__category',
        ).prefetch_related(
            Prefetch(
                'product_fabrics',
                queryset=MarketplaceProductFabric.objects.filter(
                    is_active=True
                ).select_related("fabric")
            )
        )
    
    @extend_schema(
        summary="Filter products (POST-based)",
        description=(
            "Filter marketplace products using POST body. "
            "Categories (names) parameter is mandatory, all other filters are optional. "
            "Returns paginated list of products with one thumbnail image each. "
            "Search includes product name, code, description, subcategory names, and fabric names."
        ),
        request=ProductFilterSerializer,
        responses={
            200: MarketplaceProductListSerializer(many=True),
            400: OpenApiResponse(description="Validation error - missing or invalid filters"),
        },
        examples=[
            OpenApiExample(
                'Filter by category names only',
                value={
                    "categories": ["Men's Fashion", "Women's Fashion"]
                },
                request_only=True,
            ),
            OpenApiExample(
                'Filter with multiple criteria',
                value={
                    "categories": ["Men's Fashion", "Women's Fashion"],
                    "is_premium": True,
                    "colors": ["Red", "Blue"],
                    "min_price": "100.00",
                    "max_price": "500.00",
                    "search": "silk shirt",
                    "sort_by": "price_low_high"
                },
                request_only=True,
            ),
            OpenApiExample(
                'Search includes subcategory and fabric names',
                value={
                    "categories": ["Men's Fashion"],
                    "search": "cotton shirts"
                },
                request_only=True,
            ),
        ],
        tags=["Marketplace V3"],
    )
    @action(detail=False, methods=['post'], url_path='filter')
    def filter_products(self, request):
        """
        POST-based product filtering endpoint.
        Accepts filters in request body and returns paginated results.
        Uses category NAMES instead of IDs for frontend convenience.
        """
        # Validate filter input
        filter_serializer = ProductFilterSerializer(data=request.data)
        
        if not filter_serializer.is_valid():
            return self.validation_error_response(
                errors=filter_serializer.errors,
                message="Invalid filter parameters."
            )
        
        validated_data = filter_serializer.validated_data
        
        # Start with base queryset (public, active products only)
        queryset = self.get_queryset()
        
        # Apply filters
        # Apply filters
        queryset = self._apply_filters(
            queryset,
            validated_data,
        )

        # Apply sorting using the same filter conditions
        queryset = self._apply_sorting(
            queryset,
            validated_data.get("sort_by", "newest"),
            validated_data,
        )
        
        # Paginate results
        paginator = self.pagination_class()
        paginated_queryset = paginator.paginate_queryset(queryset, request)
        
        # Serialize
        serializer = self.get_serializer(paginated_queryset, many=True)
        
        # Return paginated response
        return paginator.get_paginated_response(serializer.data)
    
    
    def _apply_filters(self, queryset, filters):
            """Apply product filters using eligible fabric relationships."""

            # 1. Category names
            category_names = filters.get("categories", [])

            if category_names:
                category_q = Q()

                for category_name in category_names:
                    category_q |= Q(
                        subcategory__category__name__iexact=category_name.strip()
                    )

                queryset = queryset.filter(category_q)

            # 2. Selling-price visibility
            show_selling_price = filters.get("show_selling_price")

            if show_selling_price is not None:
                queryset = queryset.filter(
                    show_selling_price=show_selling_price
                )

            # 3. Premium status
            is_premium = filters.get("is_premium")

            if is_premium is not None:
                queryset = queryset.filter(is_premium=is_premium)

            # 4. Colors
            colors = filters.get("colors")

            if colors:
                color_q = Q()

                for color in colors:
                    color_q |= Q(color__iexact=color.strip())

                queryset = queryset.filter(color_q)

            # Filter by model names (case-insensitive, multiple values = OR)
            model_names = filters.get("models")

            if model_names:
                model_q = Q()

                for model_name in model_names:
                    model_q |= Q(model__iexact=model_name.strip())

                queryset = queryset.filter(model_q)

            # 5. Subcategories
            subcategory_ids = filters.get("subcategory_ids")

            if subcategory_ids:
                queryset = queryset.filter(
                    subcategory_id__in=subcategory_ids
                )

            # 6. Fabric and price filters.
            # All conditions must match the SAME fabric relationship.
            fabric_ids = filters.get("fabric_ids") or []
            min_price = filters.get("min_price")
            max_price = filters.get("max_price")

            if fabric_ids or min_price is not None or max_price is not None:

                eligible_fabrics = MarketplaceProductFabric.objects.filter(
                    product_id=OuterRef("pk"),
                    is_active=True,
                    fabric__is_active=True,
                )

                if fabric_ids:
                    eligible_fabrics = eligible_fabrics.filter(
                        fabric_id__in=fabric_ids
                    )

                if min_price is not None:
                    eligible_fabrics = eligible_fabrics.filter(
                        selling_price__gte=min_price
                    )

                if max_price is not None:
                    eligible_fabrics = eligible_fabrics.filter(
                        selling_price__lte=max_price
                    )

                queryset = queryset.filter(
                    Exists(eligible_fabrics)
                )

            # 7. Search
            search = filters.get("search", "").strip()

            if search:
                queryset = queryset.filter(
                    Q(name__icontains=search)
                    | Q(product_code__icontains=search)
                    | Q(description__icontains=search)
                    | Q(subcategory__name__icontains=search)
                    | Q(
                        product_fabrics__fabric__name__icontains=search,
                        product_fabrics__is_active=True,
                        product_fabrics__fabric__is_active=True,
                    )
                )

            return queryset.distinct()

    
    
    def _apply_sorting(self, queryset, sort_by, filters=None):
            """Sort products using eligible fabric prices."""

            filters = filters or {}

            if sort_by in ("price_low_high", "price_high_low"):

                # Start with active fabric relationships.
                fabric_filter = Q(
                    product_fabrics__is_active=True,
                    product_fabrics__fabric__is_active=True,
                )

                # Apply the same fabric selection used by filtering.
                fabric_ids = filters.get("fabric_ids") or []

                if fabric_ids:
                    fabric_filter &= Q(
                        product_fabrics__fabric_id__in=fabric_ids
                    )

                min_price = filters.get("min_price")
                max_price = filters.get("max_price")

                if min_price is not None:
                    fabric_filter &= Q(
                        product_fabrics__selling_price__gte=min_price
                    )

                if max_price is not None:
                    fabric_filter &= Q(
                        product_fabrics__selling_price__lte=max_price
                    )

                if sort_by == "price_low_high":
                    return queryset.annotate(
                        sort_price=Min(
                            "product_fabrics__selling_price",
                            filter=fabric_filter,
                        )
                    ).order_by(
                            F("sort_price").asc(nulls_last=True),
                            "-id",
                        )

                return queryset.annotate(
                    sort_price=Max(
                        "product_fabrics__selling_price",
                        filter=fabric_filter,
                    )
                ).order_by(
                    F("sort_price").desc(nulls_last=True),
                    "-id",
                )

            sort_mapping = {
                "newest": "-published_at",
                "oldest": "published_at",
                "name_asc": "name",
                "name_desc": "-name",
            }

            return queryset.order_by(
                sort_mapping.get(sort_by, "-published_at"),
                "-id",
            )

    
    @extend_schema(
        summary="Get product details",
        description=(
            "Retrieve detailed information about a single product including "
            "all images, videos, fabrics, and complete specifications."
        ),
        responses={
            200: MarketplaceProductDetailSerializer,
            404: OpenApiResponse(description="Product not found or not public"),
        },
        tags=["Marketplace V3"],
    )
    def retrieve(self, request, pk=None):
        """
        Retrieve single product details.
        Returns complete product information with all images and videos.
        """
        try:
            product = self.get_queryset().get(pk=pk)
        except MarketplaceProduct.DoesNotExist:
            return self.not_found_response(
                resource="Product",
                message="Product not found or not available."
            )
        
        serializer = self.get_serializer(product)
        return self.success_response(
            data=serializer.data,
            message="Product retrieved successfully."
        )
    
    @extend_schema(
        summary="List all products (GET - deprecated)",
        description=(
            "Legacy GET endpoint for listing products. "
            "Use POST /products/filter/ instead for better filtering capabilities."
        ),
        responses={
            200: MarketplaceProductListSerializer(many=True),
        },
        tags=["Marketplace V3"],
        deprecated=True,
    )
    def list(self, request):
        """
        Simple list endpoint without filters.
        Returns all public, active products.
        """
        queryset = self.get_queryset()
        
        # Paginate
        paginator = self.pagination_class()
        paginated_queryset = paginator.paginate_queryset(queryset, request)
        
        # Serialize
        serializer = self.get_serializer(paginated_queryset, many=True)
        
        # Return paginated response
        return paginator.get_paginated_response(serializer.data)
