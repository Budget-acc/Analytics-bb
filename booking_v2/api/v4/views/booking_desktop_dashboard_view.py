from collections import defaultdict, OrderedDict
from datetime import datetime, date
from django.db.models import F, Value, Q, Exists, OuterRef, Case, When, IntegerField, Count
from django.utils.timezone import localtime, localdate, make_aware, now
from ERP.utils.response_utils import ResponseStatus, ResponseMessage
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import BookingV2, Client, BookedProductVariant
from ERP.utils.mixins import StandardResponseMixin
from shop.models import Shop
from django.utils import timezone
from booking_v2.api.v4.serializers.booking import BookingListSerializer
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from itertools import groupby
# TODO: Re-enable when Tailor Order feature is implemented
# from tailored.models import TailorOrderItem
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


class BookingDesktopDashboardView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Booking Management V4"],
        summary="Get desktop booking dashboard",
        description=(
            "Returns booking counters plus paginated upcoming and ongoing lists for desktop booking management.\n\n"
            "Purpose: Power the desktop dashboard with all active booking sections in a single call.\n\n"
            "Context: Supports independent pagination for upcoming bookings via query params."
        ),
        parameters=[
            OpenApiParameter(
                name="upcoming_page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                default=1,
                description=(
                    "Purpose: Page number for the upcoming bookings list. "
                    "Location: query parameter. "
                    "Format/Type: integer >= 1. "
                    "Optional. Default: 1."
                ),
            ),
            OpenApiParameter(
                name="upcoming_page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                default=15,
                description=(
                    "Purpose: Number of upcoming items per page. "
                    "Location: query parameter. "
                    "Format/Type: integer 1–100. "
                    "Optional. Default: 15."
                ),
            ),
        ],
        responses={
            200: inline_serializer(
                name="BookingDesktopDashboardSuccessV4",
                fields={
                    "status": serializers.CharField(help_text="success"),
                    "message": serializers.CharField(help_text="Dashboard data fetched successfully."),
                    "data": serializers.DictField(
                        help_text=(
                            "Contains: total_completed, total_upcoming, expired_count, alteration_booking_count, "
                            "upcoming_bookings (paginated), ongoing_bookings."
                        )
                    ),
                },
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
            # ------------------ ACTIVE SHOP ------------------
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )

            shop = Shop.objects.only("id").get(id=shop_id)

            # ------------------ DATE SETUP ------------------
            now = timezone.localtime(timezone.now())
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today = today_start.date()

            # ------------------ BASE BOOKINGS QUERY ------------------
            bookings_qs = (
                BookingV2.objects.filter(shop=shop)
                .exclude(delivery_status__in=["returned", "cancelled"])
                .select_related("client_identity", "staff")
                .prefetch_related("booked_items")
            )

            # Unexcluded base for Upcoming/Ongoing: a booking with
            # booking_status="upcoming" must appear regardless of
            # delivery_status (e.g. a courier return/cancellation processed
            # while the booking itself is still "upcoming" is a valid state).
            bookings_all = (
                BookingV2.objects.filter(shop=shop)
                .select_related("client_identity", "staff")
                .prefetch_related("booked_items")
            )

            # ------------------  COUNTS ------------------
            # Same conditions as desktop-list status_counts["completed"] /
            # ["expired"] (booking_v2/api/v5/views/booking_viewset/desktop_list.py)
            # so the counts match. Runs on the unexcluded base: no
            # delivery_status exclusion for either count.
            counts = BookingV2.objects.filter(shop=shop).aggregate(
                completed=Count("id", filter=Q(booking_status="completed")),
                expired=Count(
                    "id",
                    filter=Q(booking_status="upcoming", return_date__date__lt=today),
                ),
            )

            total_completed = counts["completed"] or 0
            expired_count = counts["expired"] or 0
            total_upcoming = bookings_all.filter(
                booking_status="upcoming", pickup_date__date__gte=today
            ).count()

            # ------------------ ALTERATION BOOKINGS ------------------
            has_dress_measurements = Exists(
                BookedProductVariant.objects.filter(
                    booking_id=OuterRef("id"),
                    deleted__isnull=True,
                    measurements__isnull=False
                )
                .exclude(Q(measurements={}) | Q(measurements=[]))
                .filter(
                    product_variant__product__shop_service__general_service__main_category__name__icontains="dress"
                )
                .only('id')
            )

            alteration_booking_count = (
                bookings_qs.filter(booking_status="upcoming")
                .filter(has_dress_measurements)
                .distinct()
                .count()
            )

            # ------------------ UPCOMING BOOKINGS ------------------
            upcoming_bookings_qs = (
                bookings_all.filter(
                    booking_status="upcoming",
                    pickup_date__date__gte=today
                )
                .order_by("pickup_date", "id")
            )

            # ------------------ ONGOING BOOKINGS ------------------
            ongoing_bookings_qs = (
                bookings_all.filter(
                    booking_status="upcoming",
                    pickup_date__date__lte=today,
                    return_date__date__gte=today
                )
                .order_by("return_date", "id")
            )

            # ------------------ TAILOR UPCOMING ------------------
            # TODO: Re-enable when Tailor Order feature is implemented
            # tailor_items_qs = (
            #     TailorOrderItem.objects.filter(
            #         order__shop=shop,
            #         order__created_booking__isnull=True,
            #         assigned_tailor__isnull=False,
            #         expected_completion_date__gte=today_start,
            #         order__deleted__isnull=True,
            #     )
            #     .select_related(
            #         "assigned_tailor",
            #         "order",
            #         "order__staff"
            #     )
            #     .only(
            #         "order__id",
            #         "order__status",
            #         "expected_completion_date",
            #         "product_name",
            #         "assigned_tailor__name",
            #         "order__staff__color",
            #         "order__staff_id",
            #     )
            #     .order_by("expected_completion_date", "order_id")
            # )

            # ------------------ GROUP TAILOR ITEMS ------------------
            # TODO: Re-enable when Tailor Order feature is implemented
            # tailor_grouped = []
            # for (order_id, tailor_name, pickup_date), group in groupby(
            #     tailor_items_qs,
            #     key=lambda x: (
            #         x.order.id,
            #         x.assigned_tailor.name,
            #         timezone.localtime(x.expected_completion_date).date(),
            #     ),
            # ):
            #     items = list(group)
            #     order = items[0].order
            #
            #     tailor_grouped.append(
            #         {
            #             "id": order_id,
            #             "tailor_name": tailor_name,
            #             "pickup_date": timezone.localtime(
            #                 items[0].expected_completion_date
            #             ).strftime("%d-%m-%Y %H:%M:%S"),
            #             "status": order.status,
            #             "products": ", ".join(i.product_name for i in items),
            #             "type": "tailor_order",
            #             "staff_color": order.staff.color if order.staff else None,
            #         }
            #     )
            tailor_grouped = []

            # ------------------ PAGINATION FOR UPCOMING ------------------
            try:
                upcoming_page_size = int(request.query_params.get('upcoming_page_size', 15))
                upcoming_page_number = int(request.query_params.get('upcoming_page', 1))
            except (ValueError, TypeError):
                upcoming_page_size = 15
                upcoming_page_number = 1

            # Validate and clamp values
            upcoming_page_number = max(1, upcoming_page_number)
            upcoming_page_size = max(1, min(100, upcoming_page_size))

            # Calculate total upcoming items (bookings only; tailor orders disabled for now)
            total_upcoming_bookings = upcoming_bookings_qs.count()
            # TODO: Re-enable when Tailor Order feature is implemented
            # total_tailor_orders = len(tailor_grouped)
            total_tailor_orders = 0
            total_upcoming_items = total_upcoming_bookings + total_tailor_orders

            # Calculate pagination indices
            upcoming_start = (upcoming_page_number - 1) * upcoming_page_size
            upcoming_end = upcoming_start + upcoming_page_size

            # TODO: Re-enable when Tailor Order feature is implemented
            # # Pagination for mixed tailor orders and bookings
            # upcoming_bookings_for_page = []
            # upcoming_tailor_for_page = []
            #
            # # Get tailor items for current page
            # if upcoming_start < total_tailor_orders:
            #     tailor_start = upcoming_start
            #     tailor_end = min(upcoming_end, total_tailor_orders)
            #     upcoming_tailor_for_page = tailor_grouped[tailor_start:tailor_end]
            #
            # # Get bookings for current page
            # if upcoming_end > total_tailor_orders:
            #     booking_start = max(0, upcoming_start - total_tailor_orders)
            #     booking_end = upcoming_end - total_tailor_orders
            #     upcoming_bookings_for_page = upcoming_bookings_qs[booking_start:booking_end]
            upcoming_bookings_for_page = upcoming_bookings_qs[upcoming_start:upcoming_end]

            # Serialize bookings
            upcoming_serialized = []
            if upcoming_bookings_for_page:
                upcoming_serialized = [
                    {**data, "type": "booking"}
                    for data in BookingListSerializer(upcoming_bookings_for_page, many=True).data
                ]

            # Combine and sort
            upcoming_combined = sorted(
                # TODO: Re-enable when Tailor Order feature is implemented
                # upcoming_tailor_for_page + upcoming_serialized,
                upcoming_serialized,
                key=lambda x: (self._parse_date(x.get("pickup_date")), x.get("id") or 0)
            )

            # ------------------ PAGINATION FOR ONGOING ------------------
            try:
                ongoing_page_size = int(request.query_params.get('ongoing_page_size', 15))
                ongoing_page_number = int(request.query_params.get('ongoing_page', 1))
            except (ValueError, TypeError):
                ongoing_page_size = 15
                ongoing_page_number = 1

            # Validate and clamp values
            ongoing_page_number = max(1, ongoing_page_number)
            ongoing_page_size = max(1, min(100, ongoing_page_size))

            # Calculate total ongoing items
            total_ongoing_items = ongoing_bookings_qs.count()

            # Calculate pagination indices
            ongoing_start = (ongoing_page_number - 1) * ongoing_page_size
            ongoing_end = ongoing_start + ongoing_page_size

            # Get ongoing bookings for this page
            ongoing_bookings_for_page = ongoing_bookings_qs[ongoing_start:ongoing_end]

            # Serialize and sort ongoing bookings
            ongoing_combined = []
            if ongoing_bookings_for_page:
                ongoing_combined = sorted(
                    [
                        {**data, "type": "booking"}
                        for data in BookingListSerializer(ongoing_bookings_for_page, many=True).data
                    ],
                    key=lambda x: (
                        self._parse_date(x.get("return_date") or x.get("pickup_date")),
                        x.get("id") or 0,
                    ),
                )

            # ------------------ FINAL RESPONSE ------------------
            data = {
                "completed_count": total_completed,
                "upcoming_count": total_upcoming,
                "expired_count": expired_count,
                "alteration_booking_count": alteration_booking_count,
                "upcoming": upcoming_combined,
                "ongoing_bookings": ongoing_combined,
                "pagination": {
                    "upcoming": {
                        "next": f"?upcoming_page={upcoming_page_number + 1}" if upcoming_end < total_upcoming_items else None,
                        "previous": f"?upcoming_page={upcoming_page_number - 1}" if upcoming_page_number > 1 else None,
                        "count": total_upcoming_items,
                        "page_size": upcoming_page_size,
                        "current_page": upcoming_page_number,
                        "total_pages": max(1, (total_upcoming_items + upcoming_page_size - 1) // upcoming_page_size) if total_upcoming_items > 0 else 1,
                        "start_index": upcoming_start + 1 if total_upcoming_items > 0 else 0,
                        "end_index": min(upcoming_end, total_upcoming_items),
                    },
                    "ongoing": {
                        "next": f"?ongoing_page={ongoing_page_number + 1}" if ongoing_end < total_ongoing_items else None,
                        "previous": f"?ongoing_page={ongoing_page_number - 1}" if ongoing_page_number > 1 else None,
                        "count": total_ongoing_items,
                        "page_size": ongoing_page_size,
                        "current_page": ongoing_page_number,
                        "total_pages": max(1, (total_ongoing_items + ongoing_page_size - 1) // ongoing_page_size) if total_ongoing_items > 0 else 1,
                        "start_index": ongoing_start + 1 if total_ongoing_items > 0 else 0,
                        "end_index": min(ongoing_end, total_ongoing_items),
                    }
                }
            }

            return Response({
                "status": ResponseStatus.SUCCESS.value,
                "message": "Desktop dashboard data fetched successfully.",
                "data": data
            })

        except Exception as e:
            logger.error(
                f"Desktop Booking dashboard error: {str(e)}",
                exc_info=True
            )
            return self.error_response(
                message="Something went wrong while fetching desktop dashboard data.",
                dev_message=str(e),
            )

    # ------------------ DATE PARSER ------------------
    @staticmethod
    def _parse_date(date_str):
        if not date_str:
            return datetime(1900, 1, 1)

        for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(date_str, fmt)
            except Exception:
                continue
        return datetime(1900, 1, 1)
