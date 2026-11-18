from collections import defaultdict, OrderedDict
from datetime import datetime
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


class PendingSecurityAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Booking Management V4"],
        summary="Get pending security amounts",
        description=(
            "Returns upcoming bookings carrying a refundable security deposit, "
            "grouped by Month → Day based on booking created date.\n\n"
            "Purpose: Used in wallet security pending views.\n\n"
            "Context: Scoped to the active shop; supports optional client filter."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Restricts security list to a specific client. "
                    "Location: query parameter. "
                    "Format/Type: integer. "
                    "Optional."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Grouped success response. Data is nested as Month → Day → [{ id, client_name, security_amount, booking_date, created_at }]."
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
            # -------- Validate shop --------
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )

            # -------- Validate client (optional) --------
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

            # -------- Fetch bookings with pending security amount --------
            security_pending_bookings = BookingV2.objects.filter(
                shop_id=shop_id,
                booking_status="upcoming",
                security_amount__gt=0
            ).select_related("client_identity").order_by("pickup_date")

            if client:
                security_pending_bookings = security_pending_bookings.filter(client_identity=client)

            # -------- Transform into list --------
            bookings_data = []
            for booking in security_pending_bookings:
                created = localtime(booking.created_at) if booking.created_at else None
                bookings_data.append({
                    "id": booking.id,
                    "client_name": booking.client_identity.client_name if booking.client_identity else None,
                    "security_amount": booking.security_amount,
                    "booking_date": created.strftime("%d-%m-%Y") if created else None,
                    "created_at": created.strftime("%d-%m-%Y %H:%M:%S") if created else None,
                })

            # -------- Group by Month → Day --------
            grouped_by_date = defaultdict(list)
            for item in bookings_data:
                created = datetime.strptime(item["created_at"], "%d-%m-%Y %H:%M:%S")
                normalized_date = created.date()

                day_key = normalized_date.strftime("%d-%m-%Y")
                month_key = normalized_date.strftime("%B %Y")
                grouped_by_date[(month_key, day_key)].append(item)

            # -------- Helpers for proper ordering --------
            def parse_month_key(month_str):
                return datetime.strptime(month_str, "%B %Y")  # e.g. "July 2025"

            def parse_day_key(day_str):
                return datetime.strptime(day_str, "%d-%m-%Y")  # e.g. "23-07-2025"

            # -------- Build Ordered dict --------
            month_day_map = defaultdict(lambda: defaultdict(list))
            for (month, day), items in grouped_by_date.items():
                month_day_map[month][day].extend(items)

            sorted_grouped = OrderedDict()
            for month in sorted(month_day_map.keys(), key=parse_month_key):  # ascending months
                sorted_grouped[month] = OrderedDict()
                for day in sorted(month_day_map[month].keys(), key=parse_day_key):  # ascending days
                    day_items = sorted(
                        month_day_map[month][day],
                        key=lambda x: datetime.strptime(x["created_at"], "%d-%m-%Y %H:%M:%S"),
                        reverse=True  # latest first inside the day
                    )
                    sorted_grouped[month][day] = {
                        "items": day_items
                    }

            # -------- Pagination (flatten month|day for page slicing) --------
            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, content in days.items():
                    flat_group_by_day[f"{month}|{day}"] = content

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # Rebuild paginated structure
            final_group = OrderedDict()
            if isinstance(paginated_group, OrderedDict):
                for key, content in paginated_group.items():
                    month, day = key.split("|")
                    if month not in final_group:
                        final_group[month] = OrderedDict()
                    final_group[month][day] = content
            else:
                final_group = sorted_grouped

            return paginator.get_paginated_response(final_group)

        except Exception as e:
            logger.error(f"PendingSecurityAmountAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching pending security amounts.",
                dev_message=str(e)
            )
