from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Shop
from shop.api.admin_v1.serializers import ShopListSerializer


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="List shops",
    description="Returns paginated shops with optional text search by name/place/address/phone fields.",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Optional search keyword."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page number."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated list of shops."),
    },
    examples=[
        OpenApiExample("Request example", value={"search": "kochi", "page": 1}, request_only=True),
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 4, "name": "Prime Tailors", "place": "Kochi"}]}, response_only=True, status_codes=["200"]),
    ],
)
def list(self, request):
    search = request.query_params.get("search", "").strip()
    queryset = Shop.objects.all()

    # Optional search filtering
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) |
            Q(place__icontains=search) |
            Q(address__icontains=search) |
            Q(phone__icontains=search) |
            Q(phone2__icontains=search)
        )

    # queryset = queryset.order_by("-created_at")
    queryset = queryset.select_related(
        "subscription", "subscription__plan", "organization"
    ).order_by("-created_at")

    # Pagination
    paginator = PageNumberPagination()
    paginated_qs = paginator.paginate_queryset(queryset, request)
    serializer = ShopListSerializer(paginated_qs, many=True)

    return paginator.get_paginated_response(serializer.data)
