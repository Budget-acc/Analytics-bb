from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.custom_pagination import StandardPageNumberPagination
from services.models import Product, ProductVariant, ShopOwnerService
from services.api.v3.serializers import ProductWithMainServiceSerializer
from shop.api.v3.utils.shop import get_active_shop_id
from django.db.models import Q, Case, When, Value, IntegerField, F, Prefetch, Exists, OuterRef
from django.db.models.functions import Lower, Length
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class ShopProductSearchAPIView(StandardResponseMixin, APIView):
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Service & Product Management V3"],
        summary="Search products in active shop",
        description="Searches active products with filters for field, price range, stock availability, and optional service scope.",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search text."),
            OpenApiParameter("search_by", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search field. Defaults to name. Invalid values are auto-fallback to name."),
            OpenApiParameter("min_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Minimum price."),
            OpenApiParameter("max_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Maximum price."),
            OpenApiParameter("shop_service_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Optional shop service id."),
            OpenApiParameter("in_stock_only", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="When true, includes only products with stock variants."),
            OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page number."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated products response."),
            400: OpenApiResponse(
                description=(
                    "Validation messages:\n"
                    "- Invalid shop service.\n"
                    "- Please enter valid numbers for price.\n"
                    "- shop_service_id: Invalid service or service does not belong to this shop.\n"
                    "- price: min_price and max_price must be integers."
                )
            ),
            404: OpenApiResponse(description="Exact message: No active services found for this shop."),
            500: OpenApiResponse(description="Exact message: An unexpected error occurred while searching products."),
        },
        examples=[
            OpenApiExample("Request example", value={"search": "shirt", "search_by": "name", "min_price": 100, "max_price": 500, "in_stock_only": True}, request_only=True),
            OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 9, "name": "Blue Shirt", "variants": [{"id": 21, "attribute": "M", "stock": 5}]}]}, response_only=True, status_codes=["200"]),
            OpenApiExample("Validation response", value={"status": "validation_error", "message": "Invalid shop service.", "errors": {"shop_service_id": ["Invalid service or service does not belong to this shop."]}}, response_only=True, status_codes=["400"]),
        ],
    )
    def get(self, request):
        try:
            shop_id = get_active_shop_id(request)
            if not shop_id:
                return self.error_response(
                    message="Active shop not found.",
                    dev_message="No active shop_id found for user.",
                )

            search = request.query_params.get("search", "").strip()
            search_by = request.query_params.get("search_by", "name")
            min_price = request.query_params.get("min_price")
            max_price = request.query_params.get("max_price")
            shop_service_id = request.query_params.get("shop_service_id")

            in_stock_only = request.query_params.get(
                "in_stock_only", "false"
            ).lower() == "true"

            valid_fields = ["name", "color", "category", "model"]
            if search_by not in valid_fields:
                search_by = "name"

            if shop_service_id:
                try:
                    shop_service = ShopOwnerService.objects.get(
                        id=shop_service_id,
                        shop_id=shop_id,
                        is_active=True
                    )
                    shop_service_ids = [shop_service.id]
                except (ShopOwnerService.DoesNotExist, ValueError):
                    return self.validation_error_response(
                        errors={
                            "shop_service_id": [
                                "Invalid service or service does not belong to this shop."
                            ]
                        },
                        message="Invalid shop service."
                    )
            else:
                shop_service_ids = list(
                    ShopOwnerService.objects.filter(
                        shop_id=shop_id,
                        is_active=True
                    )
                    .exclude(
                        general_service__main_category__name__iexact="Materials"
                    )
                    .values_list("id", flat=True)
                )

                if not shop_service_ids:
                    return self.not_found_response(
                        resource="ShopOwnerService",
                        message="No active services found for this shop.",
                        dev_message=f"No active ShopOwnerService for shop_id {shop_id}"
                    )

            filter_kwargs = {
                "shop_service__in": shop_service_ids,
                "is_active": True,
            }

            # if search:
            #     filter_kwargs[f"{search_by}__icontains"] = search

            try:
                if min_price is not None:
                    min_price = int(min_price)
                if max_price is not None:
                    max_price = int(max_price)
            except ValueError:
                return self.validation_error_response(
                    errors={"price": ["min_price and max_price must be integers."]},
                    message="Please enter valid numbers for price."
                )

            price_filter = Q()
            if min_price is not None and max_price is not None:
                price_filter = Q(price__isnull=True) | Q(price__gte=min_price, price__lte=max_price)
            elif min_price is not None:
                price_filter = Q(price__isnull=True) | Q(price__gte=min_price)
            elif max_price is not None:
                price_filter = Q(price__isnull=True) | Q(price__lte=max_price)

           # ---------- Variant Prefetch ----------
            variant_qs = ProductVariant.objects.filter(is_active=True)

            if in_stock_only:
                variant_qs = variant_qs.filter(stock__gt=0)

            # ---------- Product Query ----------
            products = (
                Product.objects
                .filter(price_filter, **filter_kwargs)
                .select_related(
                    "shop_service__general_service__main_category"
                )
                .annotate(
                    general_service_name=F("shop_service__general_service__name"),
                    main_service_name=F("shop_service__general_service__main_category__name"),
                )
                .prefetch_related(
                    Prefetch("variants", queryset=variant_qs)
                )
            )

            # Remove products without valid variants
            if in_stock_only:
                available_variants = ProductVariant.objects.filter(
                    product_id=OuterRef("pk"),
                    is_active=True,
                    stock__gt=0
                )

                products = products.annotate(
                    has_stock=Exists(available_variants)
                ).filter(has_stock=True)

            if search:
                search_by_clean = search_by.strip().lower()
                search_field_q = Q(**{f"{search_by_clean}__icontains": search})

                gadget_service_q = (
                    Q(shop_service__general_service__main_category__name__icontains="gadget") |
                    Q(shop_service__general_service__name__icontains="gadget")
                )

                # For gadget services, include variant attribute search along with product field search.
                products = products.filter(
                    search_field_q |
                    (
                        gadget_service_q &
                        Q(variants__is_active=True, variants__attribute__icontains=search)
                    )
                ).distinct().annotate(
                    search_rank=Case(
                        # 1️.Exact match
                        When(
                            **{f"{search_by_clean}__iexact": search},
                            then=Value(0)
                        ),
                        # 2️.Starts with
                        When(
                            **{f"{search_by_clean}__istartswith": search},
                            then=Value(1)
                        ),
                        # 3.Contains
                        default=Value(2),
                        output_field=IntegerField(),
                    ),
                    field_length=Length(search_by_clean),
                ).order_by(
                    "search_rank",       # relevance
                    "-id",                # most recent first among same rank
                    "field_length",      # shorter first
                    Lower(search_by_clean),    # stable alphabetical
                )
            else:
                products = products.order_by("-id")

            # products = products.prefetch_related("variants")

            paginator = StandardPageNumberPagination()
            paginated_products = paginator.paginate_queryset(products, request)
            serializer = ProductWithMainServiceSerializer(paginated_products, many=True)
            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            logger.error(f"Shop product search error: {str(e)}", exc_info=True)
            return self.error_response(
                message="An unexpected error occurred while searching products.",
                dev_message=str(e),
            )
