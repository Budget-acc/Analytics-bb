from collections import defaultdict, OrderedDict
from datetime import datetime
from django.db.models import F, Value
from django.db.models.functions import Coalesce
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import BookingV2, Client
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from booking_v2.api.v4.views.pagination import DayGroupedPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
import logging

logger = logging.getLogger("api_requests")


class PendingBookingAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Booking Management V4"],
        summary="Get pending booking amounts",
        description=(
            "Returns bookings that still have a pending payable amount (total − discount − advance), "
            "grouped by Month → Day based on return date.\n\n"
            "Purpose: Used in wallet pending dues views to show outstanding balances.\n\n"
            "Context: Scoped to the active shop; supports optional client filter."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Restricts pending amounts to a specific client. "
                    "Location: query parameter. "
                    "Format/Type: integer. "
                    "Optional."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Grouped success response. Data is nested as Month → Day → [{ booking_id, client_name, remaining_amount, return_date }]."
                )
            ),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages (one per case):\n"
                    "- Client ID must be a valid integer.\n"
                    "- Client not found for this shop."
                )
            ),
            403: OpenApiResponse(
                description="Permission error.\n\nExact message:\n- Your access to this shop has been deactivated."
            ),
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

            # Group by Month → Day
            grouped_by_date = defaultdict(list)
            for booking in pending_bookings:
                remaining_amount = booking.total_amount - booking.advance_amount - (booking.discount_amount or 0)
                local_return_date = localtime(booking.return_date) if booking.return_date else None
                normalized_date = local_return_date.date() if local_return_date else None

                date_key = normalized_date.strftime("%d-%m-%Y") if normalized_date else "Unknown"
                month_key = normalized_date.strftime("%B %Y") if normalized_date else "Unknown"

                grouped_by_date[(month_key, date_key, normalized_date)].append({
                    "booking_id": booking.id,
                    "client_name": booking.client_identity.client_name if booking.client_identity else None,
                    "remaining_amount": remaining_amount,
                    "return_date": date_key if normalized_date else None
                })

            # Sort months (ascending) then days inside each month (ascending)
            def get_sort_tuple(item):
                month, day, dt = item
                if dt:
                    return (dt.year, dt.month, dt.day)
                return (0, 0, 0)

            sorted_dates = sorted(grouped_by_date.keys(), key=get_sort_tuple)

            # Organize into months → days → list + daily sum
            sorted_grouped = OrderedDict()
            for month, day, dt in sorted_dates:
                if month not in sorted_grouped:
                    sorted_grouped[month] = OrderedDict()
                day_items = grouped_by_date[(month, day, dt)]
                total_remaining = sum(item["remaining_amount"] for item in day_items)
                sorted_grouped[month][day] = {
                    "total_pending": total_remaining,
                    "bookings": day_items
                }

            # Flatten into "month|day" keys for pagination
            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, items in days.items():
                    flat_group_by_day[f"{month}|{day}"] = items

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # Rebuild final grouped structure
            final_group = OrderedDict()
            if isinstance(paginated_group, OrderedDict):
                for key, items in paginated_group.items():
                    month, day = key.split("|")
                    if month not in final_group:
                        final_group[month] = OrderedDict()
                    final_group[month][day] = items
            else:
                final_group = sorted_grouped

            return paginator.get_paginated_response(final_group)

        except Exception as e:
            logger.error(f"Pending booking amount error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching pending booking amounts.",
                dev_message=str(e)
            )
