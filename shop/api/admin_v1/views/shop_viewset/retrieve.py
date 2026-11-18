from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count, Q
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Shop
from shop.api.admin_v1.serializers import ShopSerializer


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Retrieve shop",
    description=(
        "Returns one shop with annotated product count and subscription id.\n\n"
        "Response includes show_prices_in_public_listing (boolean): whether product prices "
        "are visible in this shop's public listing."
    ),
    parameters=[
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop id."),
    ],
    responses={
        200: OpenApiResponse(description="Shop detail response."),
        404: OpenApiResponse(description="Exact message: Shop not found."),
    },
    examples=[
        OpenApiExample("Success response", value={"id": 4, "name": "Prime Tailors", "subscription_id": 10, "total_product_count": 88, "show_prices_in_public_listing": True}, response_only=True, status_codes=["200"]),
        OpenApiExample("Not found", value={"error": "Shop not found."}, response_only=True, status_codes=["404"]),
    ],
)
def retrieve(self, request, pk=None):
    try:
        # Annotate with total product count across all services
        shop = Shop.objects.select_related("subscription", "organization").annotate(
            total_product_count=Count(
                'services__products',
                filter=Q(
                    services__products__deleted__isnull=True,
                    services__deleted__isnull=True,
                    services__is_active=True
                ),
                distinct=True
            )
        ).get(pk=pk)

        serializer = ShopSerializer(shop)
        data = serializer.data

        if hasattr(shop, 'subscription') and shop.subscription:
            data['subscription_id'] = shop.subscription.id
        else:
            data['subscription_id'] = None
        return Response(data, status=status.HTTP_200_OK)
    except Shop.DoesNotExist:
        return Response({"error": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)
