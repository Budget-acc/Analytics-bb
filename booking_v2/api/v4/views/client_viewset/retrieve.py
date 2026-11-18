from booking_v2.models import Client
from drf_spectacular.utils import OpenApiResponse, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V4"],
    summary="Get client details",
    description=(
        "Returns the full profile of a single client by client ID within the active shop.\n\n"
        "Purpose: Load client details in booking and client profile screens.\n\n"
        "Context: Use when a user selects a specific client from the list."
    ),
    responses={
        200: OpenApiResponse(
            description="Success response with client detail payload (id, client_name, phone_1_e164, phone_2_e164, created_at)."
        ),
        404: OpenApiResponse(
            description="Not found.\n\nExact message:\n- Client not found."
        ),
        500: OpenApiResponse(
            description="Server error.\n\nExact message:\n- Failed to retrieve client."
        ),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        shop_id = self.shop_id

        client = Client.objects.get(pk=pk, shop=shop_id)
        data = {
            "id": client.id,
            "client_name": client.client_name,
            "phone_1_e164": str(client.phone_1_e164) if client.phone_1_e164 else None,
            "phone_2_e164": str(client.phone_2_e164) if client.phone_2_e164 else None,
            "created_at": client.created_at,
        }
        return self.success_response(
            data=data,
            message="Client details fetched successfully."
        )
    except Client.DoesNotExist:
        return self.not_found_response(
            resource="Client",
            message="Client not found.",
            dev_message=f"Client with id {pk} not found for shop {shop_id}."
        )
    except Exception as e:
        logger.error(f"Failed to retrieve client: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to retrieve client.",
            dev_message=str(e)
        )
