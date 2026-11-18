from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.custom_pagination import StandardPageNumberPagination
from booking_v2.models import BookingV2, BookedProductVariant, Client
from shop.api.admin_v1.utils.shop import get_active_shop_id
from django.db.models import Prefetch
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class BookingDetailAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Booking Management V3"],
        summary="Get booking amount details",
        description=(
            "Returns booking-wise amount details including adjusted total, advance, booked date, and booked items.\n\n"
            "Purpose: Used in wallet booking amount drilldown.\n\n"
            "Context: Active shop scope with optional client filter."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Restricts amount details to one client. "
                    "Location: query parameter. Format/Type: integer. Optional."
                ),
            )
        ],
        responses={
            200: OpenApiResponse(description="Paginated success response with booking amount rows."),
            400: OpenApiResponse(
                description="Validation errors exact messages: Client ID must be a valid integer.; Client not found for this shop."
            ),
            403: OpenApiResponse(description="Permission error exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Server error exact message: An error occurred. Please try again."),
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

            # Optional client filter
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
                bookings = BookingV2.objects.filter(shop_id=shop_id, client_identity=client, deleted__isnull=True)
            else:
                bookings = BookingV2.objects.filter(shop_id=shop_id, deleted__isnull=True)

            bookings = bookings.select_related("client_identity").prefetch_related(
                Prefetch(
                    "booked_items",
                    queryset=BookedProductVariant.objects.filter(deleted__isnull=True),
                    to_attr="non_deleted_items"
                )
            ).order_by("-created_at")

            response_data = []
            for booking in bookings:
                discount_amount = booking.discount_amount or 0
                effective_total_amount = booking.total_amount - discount_amount
                items = [item.product_name for item in getattr(booking, "non_deleted_items", [])]
                response_data.append({
                    "booking_id": booking.id,
                    "client_name": booking.client_identity.client_name if booking.client_identity else None,
                    # "total_amount": booking.total_amount,
                    "total_amount": effective_total_amount,
                    "advance_amount": booking.advance_amount,
                    "booked_date": booking.created_at.strftime("%d-%m-%Y %H:%M:%S") if booking.created_at else None,
                    "booked_items": items,
                })

            # Apply pagination
            paginator = StandardPageNumberPagination()
            paginated_data = paginator.paginate_queryset(response_data, request)
            return paginator.get_paginated_response(paginated_data)

        except Exception as e:
            logger.error(f"Booking amounts: {str(e)}", exc_info=True)
            return self.error_response(
                message="An error occurred. Please try again.",
                dev_message=str(e)
            )


__all__ = ["BookingDetailAmountAPIView"]
