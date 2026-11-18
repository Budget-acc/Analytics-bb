from booking_v2.models import BookingV2, BookedProductVariant
from booking_v2.api.v4.serializers.booking import BookingDetailSerializer, ClientRequirementV2Serializer
from booking_v2.api.v4.serializers.product import BookedProductVariantSerializer
from drf_spectacular.utils import OpenApiResponse, extend_schema
import logging
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V4"],
    summary="Get booking details",
    description=(
        "Returns full booking detail for a given booking ID within the active shop.\n\n"
        "Purpose: Load the booking detail page with booking data, booked items, and client requirements.\n\n"
        "Context: Use after selecting a booking from the list or dashboard."
    ),
    responses={
        200: OpenApiResponse(
            description="Success response with booking, booked_items, and client_requirements sections."
        ),
        404: OpenApiResponse(
            description="Not found.\n\nExact message:\n- Booking not found."
        ),
        500: OpenApiResponse(
            description="Server error.\n\nExact message:\n- Something went wrong while fetching booking details."
        ),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        shop_id = self.shop_id

        booking = (
            BookingV2.objects.select_related(
                "client_identity", "service__general_service__main_category"
            )
            .prefetch_related("booked_items", "requirements")
            .filter(id=pk, shop_id=shop_id)
            .first()
        )

        if not booking:
            return self.not_found_response(
                resource="Booking",
                message="Booking not found.",
                dev_message=f"Booking with id {pk} not found for shop {shop_id}."
            )

        booking_data = BookingDetailSerializer(booking).data
        booked_items = BookedProductVariantSerializer(
            booking.booked_items.all(), many=True
        ).data
        client_req = booking.requirements.first()
        client_req_data = (
            ClientRequirementV2Serializer(client_req).data if client_req else {}
        )

        return self.success_response(
            data={
                "booking": booking_data,
                "booked_items": booked_items,
                "client_requirements": client_req_data,
            },
            message="Booking details fetched successfully."
        )
    except Exception as e:
        logger.error(f"Booking retrieve error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching booking details.",
            dev_message=str(e)
        )
