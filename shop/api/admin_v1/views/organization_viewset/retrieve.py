from django.db.models import Count
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from shop.models import Organization
from shop.api.admin_v1.serializers import OrganizationSerializer


@extend_schema(
    tags=["Organization Admin Management V1"],
    summary="Retrieve organization",
    description=(
        "Returns the full details of a single organization by its ID.\n\n"
        "Purpose: Allows admins to view all details of a specific organization including "
        "its public token, active status, and the number of shops linked to it.\n\n"
        "Authentication: JWT — admin users only.\n\n"
        "Path parameters — Required:\n"
        "- id (integer): The database ID of the organization."
    ),
    parameters=[
        OpenApiParameter(
            name="id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the organization to retrieve. "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Organization detail.\n\n"
                "Response fields: id, name, slug, public_token, is_active, "
                "public_listing_enabled, show_prices_in_public_listing, created_at, shop_count."
            )
        ),
        404: OpenApiResponse(description="Exact message: Organization not found."),
    },
    examples=[
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Organization retrieved successfully.",
                "data": {
                    "id": 1,
                    "name": "Royal Wedding Rentals",
                    "slug": "royal-wedding-rentals",
                    "public_token": "X7h29LmQa3",
                    "is_active": True,
                    "public_listing_enabled": True,
                    "show_prices_in_public_listing": True,
                    "created_at": "2026-06-19T10:00:00Z",
                    "shop_count": 3,
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Not found",
            value={"status": "error", "message": "Organization not found."},
            response_only=True,
            status_codes=["404"],
        ),
    ],
)
def retrieve(self, request, pk=None):
    try:
        org = (
            Organization.objects
            .prefetch_related("shops")
            .annotate(shop_count=Count("shops"))
            .get(pk=pk)
        )
        return self.success_response(
            data=OrganizationSerializer(org).data,
            message="Organization retrieved successfully.",
        )
    except Organization.DoesNotExist:
        return self.not_found_response(resource="Organization")
