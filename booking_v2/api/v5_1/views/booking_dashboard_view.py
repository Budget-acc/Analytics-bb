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
from booking_v2.api.v5_1.serializers.booking import BookingListSerializer
from booking_v2.api.v5.views.booking_viewset.list import status_filters
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from itertools import groupby
from tailored.models import TailorOrderItem, TailorOrder, TailorOrderItemAssignment
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")

EXCLUDED_TAILOR_STATUSES = [
    "cancelled",
    "returned",
    "completed",
]


class BookingDashboardView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Booking Management V5"],
        summary="Get booking dashboard",
        description=(
            "Returns booking dashboard counters and a paginated list for upcoming or ongoing views. "
            "Also includes tailor items in upcoming mode.\n\n"
            "data.data.status_counts (object of integers): upcoming, returns, pending, not_returned, expired, "
            "completed, cancelled, all - total bookings in each tab of GET /api/v5/bookings/bookings/, using "
            "the same tab rules. Independent of type and pagination."
        ),
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                default="upcoming",
                description="Purpose: dashboard mode. Allowed values: upcoming, ongoing. Optional.",
            ),
        ],
        responses={
            200: inline_serializer(
                name="BookingDashboardV5Response",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(help_text="Dashboard data fetched successfully."),
                    "count": serializers.IntegerField(required=False),
                    "next": serializers.CharField(required=False, allow_null=True),
                    "previous": serializers.CharField(required=False, allow_null=True),
                    "results": serializers.DictField(required=False),
                },
            ),
            400: OpenApiResponse(
                description="Validation errors.\n\nExact messages:\n- Invalid parameter.\n- Invalid type '{view_type}'. Allowed values: ['upcoming', 'ongoing']."
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
            expired_count = bookings.filter(
                booking_status="upcoming", return_date__date__lt=today
            ).count()
            total_upcoming = bookings.filter(
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

            # --- Tab badges of the v5 booking list (same rules, one query) ---
            status_counts = BookingV2.objects.filter(shop_id=shop.id).aggregate(
                **{key: Count("id", filter=rule) for key, rule in status_filters(today).items()}
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
                bookings_source = bookings.filter(
                    booking_status="upcoming",
                    pickup_date__date__lte=today,
                    return_date__date__gte=today,
                ).order_by("return_date", "id")
                tailor_items_qs = TailorOrderItem.objects.none()

                rental_tailor_upcoming_qs = (
                    TailorOrder.objects.filter(
                        shop=shop,
                        customer_order_type="rental",
                        created_booking__isnull=True,
                        pickup_date__date__lte=today,
                        return_date__date__gte=today,
                        deleted__isnull=True,
                    )
                    .exclude(status__in=EXCLUDED_TAILOR_STATUSES)
                    .select_related("client", "staff")
                    .prefetch_related("items")
                    .order_by("return_date", "id")
                )

            else:  # upcoming
                bookings_source = bookings.filter(
                    booking_status="upcoming",
                    pickup_date__date__gte=today,
                ).order_by("pickup_date", "id")

                # Source 1: TailorOrderItemAssignment — new multi-tailor path
                # Each assignment row has its own expected_completion_date per tailor.
                assignment_items_qs = (
                    TailorOrderItemAssignment.objects.filter(
                        order_item__order__shop=shop,
                        order_item__order__created_booking__isnull=True,
                        order_item__order__deleted__isnull=True,
                        order_item__deleted__isnull=True,
                        expected_completion_date__isnull=False,
                        expected_completion_date__gte=today_start,
                    )
                    .exclude(order_item__order__status__in=EXCLUDED_TAILOR_STATUSES)
                    .select_related("tailor", "order_item__order", "order_item__order__staff")
                )

                # Source 2: TailorOrderItem — legacy path (items with NO assignment rows)
                tailor_items_qs = (
                    TailorOrderItem.objects.filter(
                        order__shop=shop,
                        order__created_booking__isnull=True,
                        assigned_tailor__isnull=False,
                        expected_completion_date__isnull=False,
                        expected_completion_date__gte=today_start,
                        order__deleted__isnull=True,
                    )
                    .exclude(order__status__in=EXCLUDED_TAILOR_STATUSES)
                    .exclude(assignments__isnull=False)  # skip items managed by new table
                    .select_related("assigned_tailor", "order", "order__staff")
                    .order_by("expected_completion_date", "order_id")
                )

                # Normalize both sources to uniform dicts
                tailor_item_dicts = []
                for asgn in assignment_items_qs:
                    tailor_item_dicts.append({
                        "order_id": asgn.order_item.order.id,
                        "order_obj": asgn.order_item.order,
                        "tailor_name": asgn.tailor.name,
                        "expected_completion_date": asgn.expected_completion_date,
                        "product_name": asgn.order_item.product_name,
                        "staff_color": asgn.order_item.order.staff.color if asgn.order_item.order.staff else None,
                    })
                for item in tailor_items_qs:
                    tailor_item_dicts.append({
                        "order_id": item.order.id,
                        "order_obj": item.order,
                        "tailor_name": item.assigned_tailor.name,
                        "expected_completion_date": item.expected_completion_date,
                        "product_name": item.product_name,
                        "staff_color": item.order.staff.color if item.order.staff else None,
                    })

                # Sort by groupby key first (order_id, tailor_name, date) then datetime for stability.
                # groupby() is a run-length encoder — items with the same key MUST be adjacent.
                tailor_item_dicts.sort(key=lambda x: (
                    x["order_id"],
                    x["tailor_name"],
                    timezone.localtime(x["expected_completion_date"]).date(),
                    x["expected_completion_date"],
                ))

                rental_tailor_upcoming_qs = (
                    TailorOrder.objects.filter(
                        shop=shop,
                        customer_order_type="rental",
                        created_booking__isnull=True,
                        pickup_date__date__gte=today,
                        deleted__isnull=True,
                    )
                    .exclude(
                        status__in=EXCLUDED_TAILOR_STATUSES
                    )                    
                    .select_related("client", "staff")
                    .prefetch_related("items")
                    .order_by("pickup_date", "id")
                )

            # --- Build tailor grouped data ---
            tailor_grouped = []
            if view_type == "upcoming":
                for (order_id, tailor_name, pickup_date), group in groupby(
                    tailor_item_dicts,
                    key=lambda x: (
                        x["order_id"],
                        x["tailor_name"],
                        timezone.localtime(x["expected_completion_date"]).date(),
                    ),
                ):
                    items = list(group)
                    order = items[0]["order_obj"]
                    expected_dt = timezone.localtime(items[0]["expected_completion_date"])
                    products = ", ".join(i["product_name"] for i in items if i["product_name"])
                    tailor_grouped.append(
                        {
                            "id": order_id,
                            "tailor_name": tailor_name,
                            "pickup_date": expected_dt.strftime("%d-%m-%Y %H:%M:%S"),
                            "status": order.status,
                            "products": products,
                            "type": "tailor_order",
                            "staff_color": items[0]["staff_color"],
                        }
                    )

            # --- Build rental tailor order list ---
            rental_tailor_list = []

            for order in rental_tailor_upcoming_qs:
                items_str = ", ".join(
                    i.product_name for i in order.items.all() if i.product_name
                )
                rental_tailor_list.append({
                    "id": order.id,
                    "client_name": order.client.client_name if order.client else None,
                    "shop_tailor_id": order.shop_tailor_id,
                    "pickup_date": (
                        timezone.localtime(order.pickup_date).strftime("%d-%m-%Y %H:%M:%S")
                        if order.pickup_date else None
                    ),
                    "return_date": (
                        timezone.localtime(order.return_date).strftime("%d-%m-%Y %H:%M:%S")
                        if order.return_date else None
                    ),                      
                    "created_at": timezone.localtime(order.created_at).strftime("%d-%m-%Y %H:%M:%S"),
                    "items": items_str,
                    "status": order.status,
                    "staff_color": order.staff.color if order.staff else None,
                    "type": "tailor_order_rental",
                })

            # --- Build the true global chronological order BEFORE pagination ---
            # Bookings must not be sliced into a page independently of tailor
            # orders - doing so lets a tailor order dated weeks later get
            # spliced into a page ahead of bookings that chronologically
            # belong on it, silently displacing those bookings to (or past)
            # the next page. Instead, build lightweight (type, id, date) keys
            # for every eligible record across both sources, sort once, and
            # only then slice the page and hydrate the objects it needs.
            sort_field = "return_date" if view_type == "ongoing" else "pickup_date"

            booking_keys = [
                ("booking", b_id, b_date)
                for b_id, b_date in bookings_source.values_list("id", sort_field)
            ]

            # item date strings were already formatted via
            # timezone.localtime(...).strftime(...), so re-parsing them
            # yields a naive *local* datetime - make it aware (not
            # localtime(), which requires an already-aware input) so it can
            # be compared against the aware datetimes in booking_keys.
            tailor_keys = [
                (
                    "tailor_order",
                    item["id"],
                    timezone.make_aware(
                        datetime.strptime(item["pickup_date"], "%d-%m-%Y %H:%M:%S")
                    ),
                )
                for item in tailor_grouped
            ]

            rental_keys = [
                (
                    "tailor_order_rental",
                    item["id"],
                    timezone.make_aware(
                        datetime.strptime(
                            item.get(sort_field) or item["pickup_date"],
                            "%d-%m-%Y %H:%M:%S",
                        )
                    ),
                )
                for item in rental_tailor_list
            ]

            combined_keys = sorted(
                booking_keys + tailor_keys + rental_keys,
                key=lambda k: (k[2], k[1]),
            )

            # Paginate the fully-ordered key list itself (a plain list -
            # DRF's paginate_queryset works on any sliceable sequence).
            paginator = StandardPageNumberPagination()
            page_keys = paginator.paginate_queryset(combined_keys, request)

            # Hydrate only the records needed for this page.
            page_booking_ids = [k[1] for k in page_keys if k[0] == "booking"]
            booking_by_id = {
                b["id"]: {**b, "type": "booking"}
                for b in BookingListSerializer(
                    bookings_source.filter(id__in=page_booking_ids), many=True
                ).data
            }
            tailor_by_id = {item["id"]: item for item in tailor_grouped}
            rental_by_id = {item["id"]: item for item in rental_tailor_list}

            lookup = {
                "booking": booking_by_id,
                "tailor_order": tailor_by_id,
                "tailor_order_rental": rental_by_id,
            }
            # A record can change state (edited/cancelled/soft-deleted)
            # between building booking_keys and re-fetching this page's
            # objects above - skip it rather than raising KeyError for what
            # would otherwise be a perfectly valid request.
            combined_list = [
                lookup[kind][kid]
                for kind, kid, _dt in page_keys
                if kid in lookup[kind]
            ]

            # Prepare data payload
            # NOTE: upcoming_count/completed_count/expired_count/
            # alteration_booking_count are all booking-only counts. The
            # top-level `count`/`total_pages` (added by get_paginated_response
            # below) reflect the combined bookings+tailor-orders feed being
            # paginated, since that's what's actually sliced per page -
            # total_items below makes that combined total explicit so
            # clients don't have to infer it from `count` alone.
            data = {
                "completed_count": total_completed,
                "upcoming_count": total_upcoming,
                "expired_count": expired_count,
                "alteration_booking_count": alteration_booking_count,
                "total_items": len(combined_keys),
                "status_counts": status_counts,
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
