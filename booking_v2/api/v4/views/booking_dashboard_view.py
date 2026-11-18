from collections import defaultdict, OrderedDict
from datetime import datetime, date
from django.db.models import F, Value, Prefetch, Q, Exists, OuterRef, Case, When, IntegerField, Count
from django.utils.timezone import localtime, localdate, make_aware, now
from ERP.utils.response_utils import ResponseStatus, ResponseMessage
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import BookingV2, Client, BookedProductVariant, PaymentDetailsV2
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.mixins import StandardResponseMixin
from shop.models import Shop
from django.utils import timezone
from booking_v2.api.v4.serializers.booking import BookingListSerializer
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from itertools import groupby
from tailored.models import TailorOrderItem
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


class BookingDashboardView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Booking Management V4"],
        summary="Get booking dashboard",
        description=(
            "Returns booking dashboard counters and a paginated booking card list for the active shop.\n\n"
            "Purpose: Power dashboard screens with completed, upcoming, expired, and alteration counts plus a filtered booking list.\n\n"
            "Context: Use after user authentication and active shop selection. "
            "Supports both upcoming (future pickups) and ongoing (today's active) views, "
            "and merges tailor order items into the upcoming list."
        ),
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                default="upcoming",
                description=(
                    "Purpose: Selects which booking section is returned in the data list. "
                    "Location: query parameter. "
                    "Format/Type: string. "
                    "Allowed values: upcoming, ongoing. "
                    "Optional. Default: upcoming."
                ),
            ),
        ],
        responses={
            200: inline_serializer(
                name="BookingDashboardSuccessV4",
                fields={
                    "status": serializers.CharField(help_text="success"),
                    "message": serializers.CharField(help_text="Dashboard data fetched successfully."),
                    "data": serializers.DictField(
                        help_text="Contains: total_completed, total_upcoming, expired_count, alteration_booking_count, bookings (paginated list)."
                    ),
                },
            ),
            400: OpenApiResponse(
                description="Validation error.\n\nExact message:\n- Invalid parameter."
            ),
            403: OpenApiResponse(
                description="Permission error.\n\nExact message:\n- Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Server error.\n\nExact message:\n- Something went wrong while fetching dashboard data."
            ),
        },
    )
    def get(self, request):
        try:
            # --- Get active shop ---
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )
            shop = Shop.objects.get(id=shop_id)

            # --- Setup timezone dates ---
            now = timezone.now()
            local_now = timezone.localtime(now)
            today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            today = today_start.date()

            # --- BOOKINGS ---
            bookings = (
                BookingV2.objects.filter(shop=shop)
                .select_related("client_identity", "staff")
                .prefetch_related("booked_items")
            )

            bookings_qs = bookings.exclude(
                delivery_status__in=["returned", "cancelled"]
            )

            total_completed = bookings.filter(booking_status="completed").count()
            expired_count = bookings_qs.filter(
                booking_status="upcoming", return_date__date__lt=today
            ).count()
            total_upcoming = bookings_qs.filter(
                booking_status="upcoming", pickup_date__date__gte=today
            ).count()

            # --- ALTERATION (MEASUREMENTS) BOOKINGS COUNT ---
            has_dress_measurements = Exists(
                BookedProductVariant.objects.filter(
                    booking_id=OuterRef('id'),
                    deleted__isnull=True,
                    measurements__isnull=False
                ).exclude(
                    Q(measurements={}) | Q(measurements=[])
                ).filter(
                    product_variant__product__shop_service__general_service__main_category__name__icontains='dress'
                )
            )

            alteration_booking_count = (
                bookings_qs
                .filter(booking_status="upcoming")
                .filter(has_dress_measurements)
                .distinct()
                .count()
            )

            # --- Query Param ---
            allowed_types = ["upcoming", "ongoing"]
            view_type = request.query_params.get("type", "upcoming").lower()

            if view_type not in allowed_types:
                return self.validation_error_response(
                    errors={"type": [f"Invalid type '{view_type}'. Allowed values: {allowed_types}."]},
                    message="Invalid parameter."
                )

            # --- Filter bookings ---
            if view_type == "ongoing":
                bookings_source = bookings_qs.filter(
                    booking_status="upcoming",
                    pickup_date__date__lte=today,
                    return_date__date__gte=today,
                ).order_by("return_date", "id")
                tailor_items_qs = TailorOrderItem.objects.none()

            else:  # upcoming
                bookings_source = bookings_qs.filter(
                    booking_status="upcoming",
                    pickup_date__date__gte=today,
                ).order_by("pickup_date", "id")

                tailor_items_qs = (
                    TailorOrderItem.objects.filter(
                        order__shop=shop,
                        order__created_booking__isnull=True,
                        assigned_tailor__isnull=False,
                        expected_completion_date__isnull=False,
                        expected_completion_date__gte=today_start,
                        order__deleted__isnull=True,
                    )
                    .select_related("assigned_tailor", "order", "order__staff")
                    .only(
                        "order__id",
                        "order__status",
                        "expected_completion_date",
                        "product_name",
                        "assigned_tailor__name",
                        "order__staff__color",
                    )
                    .order_by("expected_completion_date", "order_id")
                )

            # --- Build tailor grouped data ---
            tailor_grouped = []
            if view_type == "upcoming":
                for (order_id, tailor_name, pickup_date), group in groupby(
                    tailor_items_qs,
                    key=lambda x: (
                        x.order.id,
                        x.assigned_tailor.name,
                        timezone.localtime(x.expected_completion_date).date(),
                    ),
                ):
                    items = list(group)
                    order = items[0].order
                    products = ", ".join(i.product_name for i in items)
                    tailor_grouped.append(
                        {
                            "id": order_id,
                            "tailor_name": tailor_name,
                            "pickup_date": timezone.localtime(items[0].expected_completion_date).strftime(
                                "%d-%m-%Y %H:%M:%S"
                            ),
                            "status": order.status,
                            "products": products,
                            "type": "tailor_order",
                            "staff_color": order.staff.color if order.staff else None,
                        }
                    )

            # --- Combine QuerySets Before Pagination ---
            # For better scalability: paginate the raw DB objects first before serializing
            combined_list = list(tailor_grouped)  # tailor orders (already serialized)

            # Only fetch bookings needed for this page
            paginator = StandardPageNumberPagination()
            booking_page = paginator.paginate_queryset(bookings_source, request)
            booking_serializer = BookingListSerializer(booking_page, many=True)
            booking_data = [{**item, "type": "booking"} for item in booking_serializer.data]

            # Merge bookings + tailor items and sort
            combined_list += booking_data

            def parse_date(item):
                # For ongoing bookings, sort by return_date
                if view_type == "ongoing":
                    date_field = item.get("return_date") or item.get("pickup_date")
                else:
                    date_field = item["pickup_date"]

                try:
                    return datetime.strptime(date_field, "%d-%m-%Y %H:%M:%S")
                except (ValueError, KeyError):
                    try:
                        return datetime.strptime(date_field, "%Y-%m-%d %H:%M:%S")
                    except (ValueError, KeyError):
                        return datetime(1900, 1, 1)

            combined_list = sorted(combined_list, key=parse_date)

            # Prepare data payload
            data = {
                "completed_count": total_completed,
                "upcoming_count": total_upcoming,
                "expired_count": expired_count,
                "alteration_booking_count": alteration_booking_count,
            }

            if view_type == "ongoing":
                data["ongoing_bookings"] = combined_list
            else:
                data["upcoming"] = combined_list

            # Keep same response format
            paginated_response = paginator.get_paginated_response(data)
            paginated_response.data["status"] = ResponseStatus.SUCCESS.value
            paginated_response.data["message"] = "Dashboard data fetched successfully."

            return paginated_response

        except Exception as e:
            logger.error(f"Booking dashboard error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching dashboard data.",
                dev_message=str(e),
            )
