import logging
from rest_framework.decorators import action
from django.db.models import Q, Case, When, Value, IntegerField
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import Product
from services.api.v3.serializers import ProductWithMainServiceSerializer
from ERP.utils.custom_pagination import StandardPageNumberPagination

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Match destination products for transfer",
    description="Finds matching active destination products for source variant transfer using name/model/category/color scoring.",
    request=inline_serializer(
        name="ServiceV3MatchProductRequest",
        fields={
            "from_variant_id": serializers.IntegerField(required=True),
            "to_shop_id": serializers.IntegerField(required=True),
        },
    ),
    responses={
        200: OpenApiResponse(description="Paginated matching products response, can be empty list when no destination service/match found."),
        403: OpenApiResponse(description="Exact message: You do not have permission to transfer stock between these shops."),
        500: OpenApiResponse(description="Exact message: Something went wrong while matching products."),
    },
    examples=[
        OpenApiExample("Request example", value={"from_variant_id": 22, "to_shop_id": 5}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 33, "name": "Blue Shirt"}]}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["post"], url_path="match-product")
def match_product(self, request):
    try:
        from_variant_id = request.data.get("from_variant_id")
        to_shop_id = request.data.get("to_shop_id")
        user = request.user

        # Get source product
        source_product, _ = self._get_source_product_and_variant(from_variant_id)

        source_shop_id = source_product.shop_service.shop.id
        if not self._check_user_permission(request.user, [source_shop_id, to_shop_id]):
            return self.permission_denied_response(
                message="You do not have permission to transfer stock between these shops."
            )

        # Check destination service and permissions
        destination_service = self._get_destination_service(
            to_shop_id, source_product.shop_service.general_service, user
        )
        paginator = StandardPageNumberPagination()

        if not destination_service:
            paginated = paginator.paginate_queryset([], request)
            return paginator.get_paginated_response([])

        # Build matching query with scoring
        source_name = source_product.name
        source_model = source_product.model or ""
        source_category = source_product.category or ""
        source_color = source_product.color or ""

        matching_products = Product.objects.filter(
            shop_service=destination_service,
            is_active=True
        ).prefetch_related("variants").annotate(
            exact_match_score=Case(
                When(
                    Q(name__iexact=source_name) &
                    Q(model__iexact=source_model) &
                    Q(category__iexact=source_category) &
                    Q(color__iexact=source_color),
                    then=Value(4)
                ),
                When(name__iexact=source_name, then=Value(3)),
                When(name__icontains=source_name, then=Value(2)),
                When(name__icontains=source_name.split()[0] if source_name else "", then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).filter(
            exact_match_score__gt=0
        ).order_by('-exact_match_score', '-id')

        if not matching_products.exists():
            paginated = paginator.paginate_queryset([], request)
            return paginator.get_paginated_response([])

        # Paginate and serialize
        paginated_products = paginator.paginate_queryset(matching_products, request)
        serializer = ProductWithMainServiceSerializer(paginated_products, many=True)

        return paginator.get_paginated_response(serializer.data)
    except Exception as e:
        logger.error(f"Match product error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while matching products.",
            dev_message=str(e)
        )
