import logging
from django.db.models import Q, Case, When, Value, IntegerField, F, TextField, Exists, OuterRef
from django.db.models.functions import Lower, Cast, Length
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product, ServiceAttributeTemplate, ServiceAttributeValue
from services.models import ShopOwnerService
from ERP.utils.custom_pagination import StandardPageNumberPagination
from services.api.v3.serializers import ProductWithMainServiceSerializer
from services.api.v3.views.product.helpers.exceptions import InvalidAttributeKeyError, ServiceMismatchError
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="List products by service",
    description="Returns paginated active products for a selected shop service with search and stock/price filters.",
    parameters=[
        OpenApiParameter("shop_service_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Required shop service id."),
        OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search text."),
        OpenApiParameter("search_by", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Product field or service attribute key."),
        OpenApiParameter("min_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Minimum price."),
        OpenApiParameter("max_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Maximum price."),
        OpenApiParameter("in_stock_only", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Only products with stock variants."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated product list response."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Please select a service to view products.\n"
                "- This field is required.\n"
                "- Please enter valid numbers for price.\n"
                "- min_price and max_price must be integers."
            )
        ),
        404: OpenApiResponse(description="Exact message: The selected service is not available or inactive."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_service_id": 8, "search": "shirt", "search_by": "name", "in_stock_only": True, "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 9, "name": "Blue Shirt"}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Missing service response", value={"status": "validation_error", "message": "Please select a service to view products.", "errors": {"shop_service_id": ["This field is required."]}}, response_only=True, status_codes=["400"]),
    ],
)
@skip_subscription_validation
def list(self, request):
    """
    List products with support for searching in:
    1. Standard product fields: name, color, category, model
    2. Service attribute fields: any attribute_key from ServiceAttributeTemplate
    """
    shop_id = self.shop_id
    shop_service_id = request.query_params.get("shop_service_id")
    search = request.query_params.get("search")
    search_by = request.query_params.get("search_by", "name")
    min_price = request.query_params.get("min_price")
    max_price = request.query_params.get("max_price")
    in_stock_only = request.query_params.get("in_stock_only", "false").lower() == "true"

    if not shop_service_id:
        return self.validation_error_response(
            errors={"shop_service_id": ["This field is required."]},
            message="Please select a service to view products."
        )

    try:
        shop_service = ShopOwnerService.objects.get(id=shop_service_id, is_active=True)
    except ShopOwnerService.DoesNotExist:
        return self.not_found_response(
            resource="ShopOwnerService",
            message="The selected service is not available or inactive."
        )

    if shop_service.shop.id != shop_id:
        return self.not_found_response(
            resource="ShopOwnerService",
            message="The selected service is not available or inactive."
        )

    general_service = shop_service.general_service
    main_category_name = ""
    if getattr(general_service, "main_category", None):
        main_category_name = (general_service.main_category.name or "").lower()
    general_service_name = (general_service.name or "").lower()
    is_gadget_service = "gadget" in main_category_name or "gadget" in general_service_name

    # Base filter for all products
    filter_kwargs = {
        "shop_service": shop_service,
        "is_active": True,
    }

    # Standard product fields (whitelist)
    standard_fields = ["name", "color", "category", "model"]

    # Build base queryset with main_service_name
    products = (
        Product.objects
        .filter(**filter_kwargs)
        .select_related(
            "shop_service__general_service__main_category"
        )
        .annotate(
            general_service_name=F("shop_service__general_service__name")
        )
    )

    order_by_args = ["-id"]

    # Apply search filter
    if search:
        # ==================== INPUT VALIDATION ====================
        # Phase 1: Sanitize search value
        search = search.strip()

        # Fail-fast: reject empty searches
        if not search:
            pass  # Empty search, skip filtering
        else:
            # Phase 2: Validate search_by parameter (Whitelist approach)
            search_by_clean = search_by.strip().lower()

            # Check against whitelist for standard fields
            if search_by_clean in standard_fields:
                # Base query on product fields
                field_query = Q(**{f"{search_by_clean}__icontains": search})

                # Gadget service should also search variant attribute
                if is_gadget_service:
                    field_query = field_query | Q(
                        variants__is_active=True,
                        variants__attribute__icontains=search
                    )

                products = (
                    products
                    .filter(field_query)
                    .annotate(
                        search_rank=Case(
                            When(**{f"{search_by_clean}__iexact": search}, then=Value(0)),
                            When(**{f"{search_by_clean}__istartswith": search}, then=Value(1)),
                            default=Value(2),
                            output_field=IntegerField(),
                        ),
                        field_length=Length(F(search_by_clean)),
                        field_lower=Lower(F(search_by_clean)),
                    )
                    .distinct()   # Required because variants join may duplicate products
                )
                order_by_args = [
                    "search_rank",
                    "field_length",
                    "field_lower",
                    "-id",
                ]

            else:
                # Phase 3: Service attribute search with strict validation
                try:
                    general_service = shop_service.general_service

                    # SECURITY: Validate attribute_key exists BEFORE using it
                    # This prevents injection attacks and DoS attempts
                    attribute_template = ServiceAttributeTemplate.objects.filter(
                        general_service=general_service,
                        attribute_key=search_by_clean,  # Parameterized query
                        applies_to='product'
                    ).select_related('general_service').first()

                    if not attribute_template:
                        raise InvalidAttributeKeyError(
                            f"Attribute key '{search_by_clean}' not found for service {general_service.id}"
                        )

                    # SECURITY: Use the validated template object
                    # Double-check service match (prevents cross-service data leaks)
                    if attribute_template.general_service.id != general_service.id:
                        logger.error(
                            f"[Product List] Service mismatch detected (security issue)",
                            extra={
                                'shop_id': shop_id,
                                'expected_service': general_service.id,
                                'attribute_service': attribute_template.general_service.id
                            }
                        )
                        raise ServiceMismatchError(
                            f"Attribute belongs to service {attribute_template.general_service.id}, "
                            f"expected {general_service.id}"
                        )

                    # Safe to use: attribute validated and service matched
                    matching_attributes = ServiceAttributeValue.objects.annotate(
                        value_text=Cast('value', TextField())
                    ).filter(
                        product=OuterRef('pk'),
                        attribute_id=attribute_template.id,  # Use PK, not string
                        value_text__icontains=search  # Parameterized
                    )

                    products = products.annotate(
                        has_matching_attribute=Exists(matching_attributes)
                    ).filter(has_matching_attribute=True)

                except (InvalidAttributeKeyError, ServiceMismatchError) as e:
                    products = products.none()
                except ServiceAttributeTemplate.DoesNotExist:
                    # Attribute template deleted between check and use (race condition)
                    products = products.none()
                except (TypeError, ValueError) as e:
                    # Data type errors (e.g., invalid Cast operations)
                    logger.error(
                        f"[Product List] Data type error in attribute search: {str(e)}",
                        exc_info=True,
                        extra={
                            'shop_id': shop_id,
                            'search_by': search_by_clean[:50],
                            'search': search[:50],
                            'error_type': type(e).__name__
                        }
                    )
                    products = products.none()
                except Exception as e:
                    # Unexpected errors - fail safe (don't expose error details to user)
                    logger.error(
                        f"[Product List] Unexpected attribute search error: {str(e)}",
                        exc_info=True,
                        extra={
                            'shop_id': shop_id,
                            'service_id': shop_service.id,
                            'search_by': search_by_clean[:50],  # Truncate to prevent log injection
                            'search': search[:50],
                            'user': request.user.id if request.user.is_authenticated else 'anonymous',
                            'error_type': type(e).__name__
                        }
                    )
                    products = products.none()

    # Safe Price range filters
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

    # Apply price filters
    if min_price is not None and max_price is not None:
        products = products.filter(
            Q(price__isnull=True) |
            Q(price__gte=min_price, price__lte=max_price)
        )
    elif min_price is not None:
        products = products.filter(
            Q(price__isnull=True) |
            Q(price__gte=min_price)
        )
    elif max_price is not None:
        products = products.filter(
            Q(price__isnull=True) |
            Q(price__lte=max_price)
        )

    # Filter for in-stock products only
    if in_stock_only:
        products = products.filter(
            variants__is_active=True,
            variants__stock__gt=0
        ).distinct()

    # Order by search relevance or most recent by default
    products = products.order_by(*order_by_args)

    # Paginate results
    paginator = StandardPageNumberPagination()
    paginated_products = paginator.paginate_queryset(products, request)
    serializer = ProductWithMainServiceSerializer(paginated_products, many=True, context={'request': request})
    return paginator.get_paginated_response(serializer.data)
