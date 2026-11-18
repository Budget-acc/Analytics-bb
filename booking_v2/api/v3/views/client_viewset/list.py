from booking_v2.models import Client
from django.db.models import Q
from ERP.utils.custom_pagination import StandardPageNumberPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V3"],
    summary="List clients",
    description=(
        "Returns paginated client list for the active shop with optional name and phone filters.\n\n"
        "Purpose: Power client picker/search in booking flows.\n\n"
        "Context: Use in authenticated booking management screens."
    ),
    parameters=[
        OpenApiParameter(
            name="name",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Purpose: Filters by client name (contains). Location: query parameter. Format/Type: string. Optional.",
        ),
        OpenApiParameter(
            name="phone",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Purpose: Filters by primary/secondary phone (contains). Location: query parameter. Format/Type: string digits. Optional.",
        ),
    ],
    responses={
        200: OpenApiResponse(description="Paginated success response with client items."),
        500: OpenApiResponse(description="Server error exact message: Failed to fetch clients."),
    },
)
@skip_subscription_validation
def list(self, request):
    try:
        shop_id = self.shop_id

        name = request.query_params.get("name", "").strip()
        phone = request.query_params.get("phone", "").strip()

        filters = Q(shop_id=shop_id)
        if name:
            filters &= Q(client_name__icontains=name.title())
        if phone:
            filters &= (
                Q(client_phone_1__icontains=phone) |
                Q(client_phone_2__icontains=phone)
            )

        clients = Client.objects.filter(filters).order_by("-id")

        paginator = StandardPageNumberPagination()
        paginated_clients = paginator.paginate_queryset(clients, request)

        data = [
            {
                "id": client.id,
                "name": client.client_name,
                "phone_1": client.client_phone_1,
                "phone_2": client.client_phone_2,
            }
            for client in paginated_clients
        ]

        return paginator.get_paginated_response(data)

    except Exception as e:
        logger.error(f"Failed to fetch clients: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to fetch clients.",
            dev_message=str(e)
        )
