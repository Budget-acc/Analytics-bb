from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Count, Q
from services.models import ShopOwnerService
from shop.models import Shop


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="List shop assigned services",
    description="Returns services assigned to a shop with total active products count per service.",
    parameters=[
        OpenApiParameter(name="shop_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=True, description="Target shop id."),
    ],
    responses={
        200: OpenApiResponse(description="Shop services list response in services field."),
        400: OpenApiResponse(description="Exact messages: Missing 'shop_id' query parameter. / Generic exception error string."),
        404: OpenApiResponse(description="Exact message: Shop not found."),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_id": 2}, request_only=True),
        OpenApiExample("Success response", value={"services": [{"id": 10, "name": "Tailoring", "description": "...", "icon": "https://...", "main_service": "Clothing", "total_products": 8, "is_active": True, "display_order": 0, "created_at": "2026-05-04T10:20:00Z"}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Missing param response", value={"error": "Missing 'shop_id' query parameter."}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["get"], url_path="shop-services")
def list_shop_services(self, request):
    try:
        shop_id = request.query_params.get("shop_id")
        if not shop_id:
            return Response({"error": "Missing 'shop_id' query parameter."}, status=400)

        try:
            shop = Shop.objects.get(id=shop_id)
        except Shop.DoesNotExist:
            return Response({"error": "Shop not found."}, status=404)

        shop_services = (
            ShopOwnerService.objects
            .filter(shop=shop)
            .select_related("general_service", "general_service__main_category")
            .annotate(
                total_products=Count(
                    'products',
                    filter=Q(
                        products__deleted__isnull=True,
                        products__is_active=True
                    )
                )
            )
            .order_by("display_order", "id")
        )

        service_data = [
            {
                "id": service.id,
                "name": service.general_service.name,
                "description": service.general_service.description,
                "icon": service.general_service.icon,
                "main_service": service.general_service.main_category.name if service.general_service.main_category else None,
                "total_products": service.total_products,
                "is_active": service.is_active,
                "display_order": service.display_order,
                "created_at": service.created_at,
            }
            for service in shop_services
        ]

        return Response({"services": service_data}, status=200)

    except Exception as e:
        return Response({"error": str(e)}, status=500)
