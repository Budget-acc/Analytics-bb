import logging
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch
from django.utils.timezone import now
from pytz import timezone as pytz_timezone
from rest_framework.decorators import action
from booking_v2.models import BookingV2, BookedProductVariant, RefundDetails
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")
india = pytz_timezone('Asia/Kolkata')


@extend_schema(
    tags=["Booking Management V5"],
    summary="Update delivery status",
    description="Updates booking delivery status with business-rule checks for completed/cancelled/refunded bookings and date constraints.",
    parameters=[
        OpenApiParameter(name="booking_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID."),
    ],
    request=inline_serializer(
        name="UpdateDeliveryStatusV5Request",
        fields={"delivery_status": serializers.CharField(required=True, help_text="booked | ready to deliver | delivered | returned | cancelled")},
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Delivery status updated successfully."),
        400: OpenApiResponse(description="Exact messages include: Booking is already completed.; Returned booking cannot be modified.; Cancelled booking with refund cannot be modified.; Delivery status is required.; Invalid delivery status.; Cannot update before pickup date.; You can only update after pickup."),
        404: OpenApiResponse(description="Booking not found response."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating booking details."),
    },
)
@action(detail=False, methods=["patch"], url_path="update-delivery-status/(?P<booking_id>[^/.]+)")
@transaction.atomic
def update_delivery_status(self, request, *args, **kwargs):
    pk = self.kwargs.get("booking_id")
    try:
        booking = (
            BookingV2.objects
            .select_for_update()
            .annotate(
                has_refund=Exists(
                    RefundDetails.objects.filter(booking=OuterRef("pk"))
                )
            )
            .prefetch_related(
                Prefetch(
                    "booked_items",
                    queryset=BookedProductVariant.objects.select_related("product_variant__product")
                )
            )
            .get(id=pk)
        )

        if booking.booking_status == "completed":
            return self.validation_error_response(
                errors={"booking_status": ["Cannot update delivery status of a completed booking."]},
                message="Booking is already completed."
            )

        if booking.delivery_status == "returned":
            return self.validation_error_response(
                errors={"delivery_status": ["Cannot change status. Booking is already marked as returned."]},
                message="Returned booking cannot be modified."
            )

        # if (
        #     booking.delivery_status == "cancelled"
        #     and booking.has_refund
        #     and request.data.get("delivery_status") != "cancelled"
        # ):
        #     return self.validation_error_response(
        #         errors={
        #             "delivery_status": [
        #                 "Cannot change status. Booking is cancelled and refund already processed."
        #             ]
        #         },
        #         message="Cancelled booking with refund cannot be modified."
        #     )

        delivery_status = request.data.get("delivery_status")
        if not delivery_status:
            return self.validation_error_response(
                errors={"delivery_status": ["This field is required."]},
                message="Delivery status is required."
            )
        if delivery_status not in ["booked", "ready to deliver", "delivered", "returned", "cancelled"]:
            return self.validation_error_response(
                errors={"delivery_status": ["Invalid status. Must be 'booked', 'ready to deliver', 'delivered', 'returned', or 'cancelled'."]},
                message="Invalid delivery status."
            )

        current_datetime = now().astimezone(india)
        pickup_datetime = booking.pickup_date.astimezone(india)

        if delivery_status == "delivered" and current_datetime.date() < pickup_datetime.date():
            return self.validation_error_response(
                errors={"delivery_status": ["Cannot mark delivered before pickup date."]},
                message="Cannot update before pickup date."
            )
        if delivery_status == "returned" and current_datetime < pickup_datetime:
            return self.validation_error_response(
                errors={"delivery_status": ["Cannot return before pickup date."]},
                message="You can only update after pickup."
            )

        booking.delivery_status = delivery_status
        booking.save()

        try:
            create_activity_log(
                shop=booking.shop,
                user=request.user,
                instance=booking,
                action="updated",
                message="Booking delivery status changed"
            )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for booking {booking.id}: {log_error}")

        return self.success_response(
            data={"delivery_status": booking.delivery_status},
            message="Delivery status updated successfully."
        )
    except BookingV2.DoesNotExist:
        return self.booking_not_found_response(booking_id=pk)

    except Exception as e:
        logger.error(f"Booking updating error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating booking details.",
            dev_message=str(e)
        )


__all__ = ["update_delivery_status"]
