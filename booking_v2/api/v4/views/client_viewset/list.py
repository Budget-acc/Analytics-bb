from django.db.models import Q, Value, Case, When, IntegerField
from django.db.models.functions import Length, Lower
from booking_v2.models import Client
from ERP.utils.custom_pagination import StandardPageNumberPagination
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")

@extend_schema(
    tags=["Booking Management V4"],
    summary="List clients",
    description=(
        "Returns a paginated client list for the active shop with optional name and phone search filters.\n\n"
        "Purpose: Powers the client picker and client search in booking flows.\n\n"
        "Context: Results are ranked (exact > starts-with > contains) for faster client lookup."
    ),
    parameters=[
        OpenApiParameter(
            name="name",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Filters clients by name with ranked relevance. "
                "Location: query parameter. "
                "Format/Type: string. "
                "Optional."
            ),
        ),
        OpenApiParameter(
            name="phone",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Filters clients by primary or secondary phone number (E.164 format). "
                "Location: query parameter. "
                "Format/Type: string of digits. "
                "Optional."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Paginated success response with client items (id, client_name, phone_1_e164, phone_2_e164)."),
        500: OpenApiResponse(description="Server error.\n\nExact message:\n- Failed to fetch clients."),
    },
)
@skip_subscription_validation
def list(self, request):
    try:
        shop_id = self.shop_id

        name = request.query_params.get("name", "").strip()
        phone = request.query_params.get("phone", "").strip()

        clients = Client.objects.filter(shop_id=shop_id)

        # NAME SEARCH RANKING
        if name:
            name = name.title()

            clients = clients.filter(
                client_name__icontains=name
            ).annotate(
                search_rank=Case(
                    When(client_name__iexact=name, then=Value(0)),       # Exact
                    When(client_name__istartswith=name, then=Value(1)),  # Startswith
                    default=Value(2),                                    # Contains
                    output_field=IntegerField(),
                ),
                field_length=Length("client_name"),
                field_lower=Lower("client_name"),
            ).order_by(
                "search_rank",
                "field_length",
                "field_lower",
                "-id",
            )

        # PHONE SEARCH RANKING
        if phone:
            phone = phone.strip()

            clients = clients.filter(
                Q(phone_1_e164__icontains=phone) |
                Q(phone_2_e164__icontains=phone)
            ).annotate(
                phone_rank=Case(
                    # Exact match
                    When(phone_1_e164__iexact=phone, then=Value(0)),
                    When(phone_2_e164__iexact=phone, then=Value(0)),

                    # Starts with
                    When(phone_1_e164__istartswith=phone, then=Value(1)),
                    When(phone_2_e164__istartswith=phone, then=Value(1)),

                    default=Value(2),
                    output_field=IntegerField(),
                )
            ).order_by(
                "phone_rank",
                "-id",
            )

        # Default ordering if no search
        if not name and not phone:
            clients = clients.order_by("-id")

        paginator = StandardPageNumberPagination()
        paginated_clients = paginator.paginate_queryset(clients, request)

        data = [
            {
                "id": client.id,
                "client_name": client.client_name,
                "phone_1_e164": str(client.phone_1_e164) if client.phone_1_e164 else None,
                "phone_2_e164": str(client.phone_2_e164) if client.phone_2_e164 else None,
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
