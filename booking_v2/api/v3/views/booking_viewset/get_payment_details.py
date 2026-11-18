from booking_v2.models import BookingV2
from django.utils.timezone import localtime
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V3"],
    summary="Get booking payment details",
    description=(
        "Returns payment history for a specific booking ID in the active shop.\n\n"
        "Purpose: Display booking-wise payment timeline and reconciliation entries.\n\n"
        "Context: Use from booking details/wallet detail screens."
    ),
    parameters=[
        OpenApiParameter(
            name="booking_id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description="Purpose: Identifies target booking for payment detail fetch. Location: path parameter. Format/Type: integer. Required.",
        )
    ],
    responses={
        200: OpenApiResponse(description="Success response with booking_id, client_name, and payments list."),
        404: OpenApiResponse(description="Not found exact message: Booking not found."),
        500: OpenApiResponse(description="Server error exact message: An error occurred while fetching payment details. Please try again."),
    },
)
@action(detail=False, methods=["get"], url_path="payment-details/(?P<booking_id>[^/.]+)")
def get_payment_details(self, request, *args, **kwargs):
    booking_id = self.kwargs.get("booking_id")
    try:
        shop_id = self.shop_id
        # Prefetch payments in one optimized query
        booking = (
            BookingV2.objects.select_related("client_identity", "shop")
            .prefetch_related("payments")
            .get(pk=booking_id, shop_id=shop_id)
        )

        payments = booking.payments.all().order_by("-created_at")

        data = [
            {
                "id": p.id,
                "amount": p.payment_amount,
                "payment_method": p.payment_method,
                "datetime": localtime(p.created_at).strftime("%d-%m-%Y %H:%M:%S"),
            }
            for p in payments
        ]

        return self.success_response(
            data={
                "booking_id": booking.id,
                "client_name": booking.client_identity.client_name,
                "payments": data,
            },
            message="Payment details fetched successfully."
        )

    except BookingV2.DoesNotExist:
        return self.not_found_response(
            resource="Booking",
            message="Booking not found.",
            dev_message=f"Booking with id {booking_id} not found for shop."
        )
    except Exception as e:
        logger.error(f"Payment details fetch failed for booking ID {booking_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="An error occurred while fetching payment details. Please try again.",
            dev_message=str(e)
        )
