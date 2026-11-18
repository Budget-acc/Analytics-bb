from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.custom_pagination import StandardPageNumberPagination
from booking_v2.models import BookingV2, Client
from shop.api.admin_v1.utils.shop import get_active_shop_id
from django.utils.timezone import localtime
from django.db.models import F, Value
from django.db.models.functions import Coalesce
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class PendingBookingAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Booking Management V3"],
        summary="Get pending booking amounts",
        description=(
            "Returns bookings that still have pending payable amount (total - discount - advance).\n\n"
            "Purpose: Used in wallet pending dues views.\n\n"
            "Context: Active shop scope, optional client filter."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Restricts pending amounts to one client. "
                    "Location: query parameter. Format/Type: integer. Optional."
                ),
            )
        ],
        responses={
            200: OpenApiResponse(description="Paginated success response with pending booking amount rows."),
            400: OpenApiResponse(
                description="Validation errors exact messages: Client ID must be a valid integer.; Client not found for this shop."
            ),
            403: OpenApiResponse(description="Permission error exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Server error exact message: Something went wrong while fetching pending booking amounts."),
        },
    )
    def get(self, request):
        try:
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )
            client_id = request.query_params.get("client_id")

            if client_id:
                if not client_id.isdigit():
                    return self.validation_error_response(
                        errors={"client_id": ["Client ID must be a valid integer."]},
                        message="Client ID must be a valid integer."
                    )
                try:
                    client = Client.objects.get(id=client_id, shop_id=shop_id)
                except Client.DoesNotExist:
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found for this shop."]},
                        message="Client not found for this shop."
                    )
            else:
                client = None

            # Get bookings where total > advance (i.e., pending exists)
            pending_bookings = BookingV2.objects.filter(
                shop_id=shop_id,
                total_amount__gt=F("advance_amount") + Coalesce(F("discount_amount"), Value(0))
            ).select_related("client_identity").order_by("return_date")

            if client:
                pending_bookings = pending_bookings.filter(client_identity=client)

            response_data = []
            for booking in pending_bookings:
                remaining_amount = booking.total_amount - booking.advance_amount - (booking.discount_amount or 0)
                response_data.append({
                    "booking_id": booking.id,
                    "client_name": booking.client_identity.client_name if booking.client_identity else None,
                    "remaining_amount": remaining_amount,
                    "return_date": localtime(booking.return_date).strftime("%d-%m-%Y") if booking.return_date else None
                })

            paginator = StandardPageNumberPagination()
            paginated_data = paginator.paginate_queryset(response_data, request)

            return paginator.get_paginated_response(paginated_data)

        except Exception as e:
            logger.error(f"Pending booking amount error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching pending booking amounts.",
                dev_message=str(e)
            )


__all__ = ["PendingBookingAmountAPIView"]
