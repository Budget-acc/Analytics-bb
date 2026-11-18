from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import Product, ShopOwnerService
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="List products with variants",
    description="Returns paginated active products with active variants for a shop owner service.",
    parameters=[
        OpenApiParameter(name="shop_owner_service_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=True, description="Shop owner service id."),
        OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Search value for selected search_key."),
        OpenApiParameter(name="search_key", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Search field. Accepted in code: name, color, model, category."),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
    ],
    responses={
        200: OpenApiResponse(description="Structured paginated success response."),
        400: OpenApiResponse(
            description=(
                "Exact messages include:\n"
                "- shop_owner_service_id parameter is required\n"
                "- shop_owner_service_id must be a valid integer\n"
                "- Oops! The search option you used isn't valid. You can search by: {', '.join(readable_keys)}.\n"
                "- Invalid search_key. Valid options are: name, color, model, category"
            )
        ),
        404: OpenApiResponse(description="Exact message: ShopOwnerService not found"),
        500: OpenApiResponse(description="Exact message: An unexpected error occurred while fetching products. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_owner_service_id": 12, "search": "shirt", "search_key": "name", "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"success": True, "shop_owner_service": {"id": 12, "shop_name": "Main Shop", "service_name": "Tailoring"}, "count": 1, "next": None, "previous": None, "results": [{"id": 2, "name": "Blue Shirt", "variants": [{"id": 5, "attribute": "M", "stock": 12}]}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Missing parameter response", value={"success": False, "message": "shop_owner_service_id parameter is required"}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["get"], url_path="products-with-variants")
def products_with_variants(self, request):
    """
    List all products and their variants for a specific shop owner service with DRF pagination.
    Query parameters:
    - shop_owner_service_id (required)
    """
    try:
        # Get and validate shop_owner_service_id
        shop_owner_service_id = request.query_params.get('shop_owner_service_id')

        if not shop_owner_service_id:
            return Response(
                {
                    "success": False,
                    "message": "shop_owner_service_id parameter is required"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate shop_owner_service_id is numeric
        try:
            shop_owner_service_id = int(shop_owner_service_id)
        except (ValueError, TypeError):
            return Response(
                {
                    "success": False,
                    "message": "shop_owner_service_id must be a valid integer"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        search_value = request.query_params.get('search', '').strip()

        VALID_SEARCH_KEYS = {'name', 'color', 'model', 'category'}

        search_key = request.query_params.get('search_key', 'name').strip().lower()
        if search_key not in VALID_SEARCH_KEYS:
            return Response(
                {"success": False, "message": f"Oops! The search option you used isn't valid. You can search by: {', '.join(readable_keys)}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate search_key
        valid_search_keys = ['name', 'color', 'model', 'category']
        if search_key not in valid_search_keys:
            return Response(
                {
                    "success": False,
                    "message": f"Invalid search_key. Valid options are: {', '.join(valid_search_keys)}"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate that the ShopOwnerService exists
        try:
            shop_owner_service = ShopOwnerService.objects.get(id=shop_owner_service_id)
        except ShopOwnerService.DoesNotExist:
            return Response(
                {
                    "success": False,
                    "message": "ShopOwnerService not found"
                },
                status=status.HTTP_404_NOT_FOUND
            )

        # Get all active products for this shop owner service
        products_queryset = Product.objects.filter(
            shop_service=shop_owner_service,
            is_active=True
        ).prefetch_related('variants').order_by('name')

        if search_value:
            search_filter = f"{search_key}__icontains"
            products_queryset = products_queryset.filter(**{search_filter: search_value})

        paginator = PageNumberPagination()
        paginated_products = paginator.paginate_queryset(products_queryset, request, view=self)


        # Serialize the data
        products_data = []
        for product in paginated_products:
            try:
                # Get active variants for this product
                variants = product.variants.filter(is_active=True).order_by('attribute')

                variants_data = []
                for variant in variants:
                    variants_data.append({
                        'id': variant.id,
                        'attribute': variant.attribute,
                        'price': variant.price,
                        'stock': variant.stock,
                        'is_active': variant.is_active,
                        'created_at': variant.created_at.isoformat() if variant.created_at else None
                    })

                products_data.append({
                    'id': product.id,
                    'name': product.name,
                    'description': product.description,
                    'color': product.color,
                    'category': product.category,
                    'model': product.model,
                    'image': product.image,
                    'thumbnail_image': product.thumbnail_image,
                    'purchase_price': product.purchase_price,
                    'price': product.price,
                    'is_active': product.is_active,
                    'created_at': product.created_at.isoformat() if product.created_at else None,
                    'variants': variants_data,
                })
            except Exception as variant_error:
                # Log individual product serialization errors but continue
                logger.error(
                    f"Error serializing product {product.id}: {str(variant_error)}",
                    exc_info=True
                )
                continue


        paginated_response = paginator.get_paginated_response(products_data)

        custom_response = {
            "success": True,
            "shop_owner_service": {
                "id": shop_owner_service.id,
                "shop_name": shop_owner_service.shop.name if hasattr(shop_owner_service, 'shop') else None,
                "service_name": shop_owner_service.general_service.name if hasattr(shop_owner_service, 'general_service') else None,
            },
            "count": paginated_response.data["count"],
            "next": paginated_response.data["next"],
            "previous": paginated_response.data["previous"],
            "results": paginated_response.data["results"],
        }

        return Response(custom_response, status=status.HTTP_200_OK)

    except Exception as e:
        # Log the internal error with full traceback
        logger.error(
            f"Unexpected error in products_with_variants: {str(e)}",
            exc_info=True,
            extra={
                'shop_owner_service_id': shop_owner_service_id,
                'user': request.user.id if hasattr(request, 'user') else None,
                'request_params': dict(request.query_params)
            }
        )

        return Response(
            {
                "success": False,
                "message": "An unexpected error occurred while fetching products. Please try again later."
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
