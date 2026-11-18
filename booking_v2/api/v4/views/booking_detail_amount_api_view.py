from collections import defaultdict, OrderedDict
from datetime import datetime
from django.db.models import Prefetch
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import BookingV2, Client, BookedProductVariant
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from booking_v2.api.v4.views.pagination import DayGroupedPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
import logging

logger = logging.getLogger("api_requests")


class BookingDetailAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        # SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["Booking Management V4"],
        summary="Get booking amount details",
        description=(
            "Returns booking-wise amount details including adjusted total, advance, security, and booked items, "
            "grouped by Month → Day based on booking created date.\n\n"
            "Purpose: Used in wallet booking amount drilldown screens.\n\n"
            "Context: Scoped to the active shop; excludes cancelled bookings; supports optional client filter."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Purpose: Restricts amount details to a specific client. "
                    "Location: query parameter. "
                    "Format/Type: integer. "
                    "Optional."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Grouped success response. Data is nested as Month → Day → "
                    "[{ booking_id, client_name, total_amount, advance_amount, "
                    "total_amount_with_security, advance_amount_with_security, items }]."
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
            500: OpenApiResponse(
                description="Server error.\n\nExact message:\n- An error occurred. Please try again."
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

            # Filter bookings
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
                bookings = BookingV2.objects.filter(shop_id=shop_id, client_identity=client, deleted__isnull=True).exclude(delivery_status="cancelled")
            else:
                bookings = BookingV2.objects.filter(shop_id=shop_id, deleted__isnull=True).exclude(delivery_status="cancelled")

            bookings = bookings.select_related("client_identity").prefetch_related(
                Prefetch(
                    "booked_items",
                    queryset=BookedProductVariant.objects.filter(deleted__isnull=True),
                    to_attr="non_deleted_items"
                )
            ).order_by("-created_at")

            # Group bookings by month and date
            grouped_by_date = defaultdict(list)
            for booking in bookings:
                local_created = localtime(booking.created_at)
                normalized_date = local_created.date() if local_created else None  # normalize date

                date_key = normalized_date.strftime("%d-%m-%Y") if normalized_date else "Unknown"
                month_key = normalized_date.strftime("%B %Y") if normalized_date else "Unknown"

                discount_amount = booking.discount_amount or 0  # Handle None as 0
                effective_total_amount = booking.total_amount - discount_amount
                security_amount = booking.security_amount or 0
                total_amount_with_security = booking.total_amount + security_amount - discount_amount
                advance_amount_with_security = (booking.advance_amount or 0) + security_amount

                items = [item.product_name for item in getattr(booking, "non_deleted_items", [])]

                grouped_by_date[(month_key, date_key)].append({
                    "booking_id": booking.id,
                    "client_name": booking.client_identity.client_name if booking.client_identity else None,
                    "total_amount": effective_total_amount,
                    "advance_amount": booking.advance_amount,
                    "total_amount_with_security": total_amount_with_security,
                    "advance_amount_with_security": advance_amount_with_security,
                    "booked_date": local_created.strftime("%d-%m-%Y %H:%M:%S"),
                    "booked_items": items,
                })

            # Sort dates in descending order
            def get_date_tuple(item):
                _, day = item
                return datetime.strptime(day, "%d-%m-%Y")

            sorted_dates = sorted(
                grouped_by_date.keys(),
                key=get_date_tuple,
                reverse=True
            )

            # Organize data → month → day → list + daily sum
            sorted_grouped = OrderedDict()
            for month, day in sorted_dates:
                if month not in sorted_grouped:
                    sorted_grouped[month] = OrderedDict()

                day_items = sorted(
                    grouped_by_date[(month, day)],
                    key=lambda x: datetime.strptime(x["booked_date"], "%d-%m-%Y %H:%M:%S"),
                    reverse=True
                )

                # Calculate daily sum of total_amount
                daily_sum = sum(item["total_amount"] for item in day_items)
                daily_sum_with_security = sum(item["total_amount_with_security"] for item in day_items)

                sorted_grouped[month][day] = {
                    "total": daily_sum,
                    "total_with_security": daily_sum_with_security,
                    "bookings": day_items
                }

            # Flatten for pagination
            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, items in days.items():
                    flat_group_by_day[f"{month}|{day}"] = items

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # Rebuild structure
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
            logger.error(f"BookingDetailAmountAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="An error occurred while fetching booking details.",
                dev_message=str(e)
            )
