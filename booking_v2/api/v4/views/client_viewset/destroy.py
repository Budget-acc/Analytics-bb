from booking_v2.models import Client, BookingV2
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiResponse, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V4"],
    summary="Delete client",
    description=(
        "Deletes a client if no active bookings are linked to them.\n\n"
        "Purpose: Keeps the client master clean while preventing deletion when active bookings exist.\n\n"
        "Context: Use for client cleanup operations."
    ),
    responses={
        200: OpenApiResponse(
            description="Success.\n\nExact message:\n- Client deleted successfully."
        ),
        400: OpenApiResponse(
            description="Validation error.\n\nExact message:\n- Delete failed: Client has active bookings."
        ),
        404: OpenApiResponse(
            description="Not found.\n\nExact message:\n- Client not found."
        ),
        500: OpenApiResponse(
            description="Server error.\n\nExact message:\n- Failed to delete client."
        ),
    },
)
def destroy(self, request, pk=None):
    try:
        shop_id = self.shop_id
        client = Client.objects.get(pk=pk, shop=shop_id)

        has_active_bookings = BookingV2.objects.filter(
            client_identity=client,
            deleted__isnull=True
        ).exists()

        if has_active_bookings:
            return self.validation_error_response(
                errors={"client": ["Cannot delete client with active bookings."]},
                message="Delete failed: Client has active bookings."
            )

        client_name = client.client_name
        client_phone = client.phone_1_e164
        client.delete()

        try:
            create_activity_log(
                shop=client.shop,
                user=request.user,
                instance=client,
                action="deleted",
                message=f"Client '{client_name}' (Phone: {client_phone}) deleted."
            )
        except Exception:
            logger.exception("Failed to create activity log for successful deletion")

        return self.success_response(
            data=None,
            message="Client deleted successfully."
        )
    except Client.DoesNotExist:
        return self.not_found_response(
            resource="Client",
            message="Client not found.",
            dev_message=f"Client with id {pk} not found for shop {shop_id}."
        )
    except Exception as e:
        logger.error(f"Failed to delete client: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to delete client.",
            dev_message=str(e)
        )
