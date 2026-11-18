from booking_v2.models import Client
from drf_spectacular.utils import OpenApiResponse, extend_schema
import logging
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V3"],
    summary="Get client details",
    description=(
        "Returns a single client profile by client ID for the active shop.\n\n"
        "Purpose: Load client details in booking and client profile flows.\n\n"
        "Context: Use when user selects a client from list."
    ),
    responses={
        200: OpenApiResponse(description="Success response with client detail payload."),
        404: OpenApiResponse(description="Not found exact message: Client not found."),
        500: OpenApiResponse(description="Server error exact message: Failed to retrieve client."),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        shop_id = self.shop_id

        client = Client.objects.get(pk=pk, shop=shop_id)
        data = {
            "id": client.id,
            "name": client.client_name,
            "phone_1": client.client_phone_1,
            "phone_2": client.client_phone_2,
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
