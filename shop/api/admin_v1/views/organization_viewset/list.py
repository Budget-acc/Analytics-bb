from rest_framework.pagination import PageNumberPagination
from django.db.models import Q, Count
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Organization
from shop.api.admin_v1.serializers import OrganizationListSerializer


@extend_schema(
    tags=["Organization Admin Management V1"],
    summary="List organizations",
    description=(
        "Returns a paginated list of all organizations.\n\n"
        "Purpose: Allows admins to browse and search all registered organizations "
        "(business groups).\n\n"
        "Authentication: JWT — admin users only.\n\n"
        "Query parameters — Optional:\n"
        "- search (string): Case-insensitive partial match applied across name and slug fields.\n"
        "- is_active (boolean — true/false): Filter by active status. If omitted, all "
        "organizations are returned regardless of active status.\n"
        "- page (integer): Page number for pagination. Default: 1.\n\n"
        "Response: Paginated result with count, next, previous, and results array. "
        "Each organization includes a shop_count field showing the number of shops linked to it."
    ),
    parameters=[
        OpenApiParameter(
            name="search",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Filter organizations by name or slug. "
                "Location: query parameter. "
                "Format/Type: string. "
                "Optional. Case-insensitive partial match."
            ),
        ),
        OpenApiParameter(
            name="is_active",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Filter by active status. "
                "Location: query parameter. "
                "Format/Type: boolean — true or false. "
                "Optional. If omitted, all organizations are returned."
            ),
        ),
        OpenApiParameter(
            name="page",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Page number for pagination. "
                "Location: query parameter. "
                "Format/Type: integer. "
                "Optional. Default: 1."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Paginated list of organizations.\n\n"
                "Response shape:\n"
                "- count (integer): Total number of organizations matching the filter.\n"
                "- next (string | null): URL for the next page. Null if last page.\n"
                "- previous (string | null): URL for the previous page. Null if first page.\n"
                "- results (array): List of organization objects. Each includes: "
                "id, name, slug, public_token, is_active, public_listing_enabled, created_at, shop_count."
            )
        ),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "count": 2,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 1,
                        "name": "Royal Wedding Rentals",
                        "slug": "royal-wedding-rentals",
                        "public_token": "X7h29LmQa3",
                        "is_active": True,
                        "public_listing_enabled": True,
                        "created_at": "2026-06-19T10:00:00Z",
                        "shop_count": 3,
                    },
                ],
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def list(self, request):
    search = request.query_params.get("search", "").strip()
    is_active_param = request.query_params.get("is_active")

    queryset = Organization.objects.annotate(shop_count=Count("shops")).order_by("-created_at")

    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(slug__icontains=search)
        )

    if is_active_param is not None:
        is_active = is_active_param.lower() == "true"
        queryset = queryset.filter(is_active=is_active)

    paginator = PageNumberPagination()
    paginated_qs = paginator.paginate_queryset(queryset, request)
    serializer = OrganizationListSerializer(paginated_qs, many=True)
    return paginator.get_paginated_response(serializer.data)
