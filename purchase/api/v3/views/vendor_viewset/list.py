from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

@extend_schema(
    tags=["Purchase Management V3"],
    summary="List vendors",
    description=(
        "Returns a paginated list of vendors for the active shop.\n\n"
        "Purpose: Lets shop staff browse/search their vendor directory - e.g. to pick an "
        "existing vendor while creating a purchase, or to manage vendor records directly.\n\n"
        "Context: Used by the purchase-entry UI's vendor picker and by any standalone vendor "
        "management screen."
    ),
    parameters=[
        OpenApiParameter(
            name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Free-text search across name, contact_number, email, and gst_number. "
                "Location: query parameter. Format/Type: string, case-insensitive substring "
                "match (OR across all four fields). Optional - omit to return all vendors."
            ),
        ),
        OpenApiParameter(
            name="ordering", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Sort order for the results. Location: query parameter. Format/Type: "
                "one of name, -name, created_at, -created_at, updated_at, -updated_at. Optional - "
                "defaults to -created_at (newest first); any other value also falls back to "
                "-created_at."
            ),
        ),
        OpenApiParameter(
            name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description="Purpose: Page number for pagination. Location: query parameter. Format/Type: integer. Optional - defaults to 1.",
        ),
        OpenApiParameter(
            name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description="Purpose: Number of vendors per page. Location: query parameter. Format/Type: integer. Optional - uses the shop's standard default page size.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Paginated list of vendors (id, name, contact_number, email, location, is_active, created_at)."),
    },
)
@skip_subscription_validation
def list(self, request, *args, **kwargs):

    queryset = self.get_queryset()

    search = request.query_params.get("search")

    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(contact_number__icontains=search)
            | Q(email__icontains=search)
            | Q(gst_number__icontains=search)
        )

    ordering = request.query_params.get(
        "ordering",
        "-created_at",
    )

    if ordering not in self.ORDERING_FIELDS:
        ordering = "-created_at"

    queryset = queryset.order_by(ordering)

    page = self.paginate_queryset(queryset)

    serializer = self.get_serializer(
        page,
        many=True,
    )

    return self.get_paginated_response(serializer.data)


__all__ = ["list"]
