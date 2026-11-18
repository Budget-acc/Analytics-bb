from datetime import datetime
from booking_v2.models import BookingV2, BookedProductVariant
from shop.models import Shop
from django.db.models import Q, Case, When, Value, IntegerField, Exists, OuterRef
from django.utils.timezone import localdate
from ERP.utils.custom_pagination import StandardPageNumberPagination
from booking_v2.api.v3.serializers.booking import BookingListSerializer
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation
import logging

logger = logging.getLogger("api_requests")


def parse_custom_date(date_str):
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


@extend_schema(
    tags=["Booking Management V3"],
    summary="List bookings",
    description=(
        "Returns paginated bookings for active shop with status/date/search filters.\n\n"
        "Purpose: Primary listing API for booking management screen.\n\n"
        "Context: Supports operational statuses and search ranking for quick lookup."
    ),
    parameters=[
        OpenApiParameter(name="status", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
                         description="Purpose: Filters booking stage. Location: query parameter. Format/Type: string. Allowed: completed, upcoming, future, past, measurements, all (all = every booking, no status filter, newest created first). Optional."),
        OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
                         description="Purpose: Free-text search across client/staff/shop_booking_id. Location: query parameter. Format/Type: string. Optional."),
        OpenApiParameter(name="start_date", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=False,
                         description="Purpose: Lower bound for pickup date filter. Location: query parameter. Format/Type: date; accepted DD-MM-YYYY or YYYY-MM-DD. Optional."),
        OpenApiParameter(name="end_date", type=OpenApiTypes.DATE, location=OpenApiParameter.QUERY, required=False,
                         description="Purpose: Upper bound for pickup date filter. Location: query parameter. Format/Type: date; accepted DD-MM-YYYY or YYYY-MM-DD. Optional."),
        OpenApiParameter(name="purchase_mode", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
                         description="Purpose: Filters bookings by purchase mode. Location: query parameter. Format/Type: string. Allowed: normal, package, courier, pickup. Optional."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated success response with booking list payload."),
        500: OpenApiResponse(description="Server error exact message: Something went wrong while fetching."),
    },
)
@skip_subscription_validation
def list(self, request):
    try:
        shop_id = self.shop_id
        shop = Shop.objects.get(id=shop_id)
        status_filter = request.query_params.get("status")
        search_query = request.query_params.get("search", "")
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")
        purchase_mode_filter = request.query_params.get("purchase_mode")

        start_date = parse_custom_date(start_date_str) if start_date_str else None
        end_date = parse_custom_date(end_date_str) if end_date_str else None

        if purchase_mode_filter:
            valid_purchase_modes = dict(BookingV2.PURCHASE_MODE_CHOICES)
            if purchase_mode_filter not in valid_purchase_modes:
                return self.validation_error_response(
                    errors={"purchase_mode": [
                        f"Invalid purchase_mode. Allowed values: {', '.join(valid_purchase_modes)}."
                    ]},
                    message="Invalid purchase_mode.",
                )

        bookings_qs = BookingV2.objects.filter(shop=shop).select_related(
            "client", "service", "client_identity", "staff"
        ).prefetch_related("booked_items")

        today = localdate()

        # Status filter ("all" matches no branch: no booking-status filter)
        if status_filter == "completed":
            bookings_qs = bookings_qs.filter(booking_status="completed")
        elif status_filter == "upcoming":
            bookings_qs = bookings_qs.filter(booking_status="upcoming")
        elif status_filter == "future":
            bookings_qs = bookings_qs.filter(booking_status="upcoming").filter(return_date__date__gte=today)
        elif status_filter == "past":
            bookings_qs = bookings_qs.filter(booking_status="upcoming").filter(return_date__date__lt=today)
        elif status_filter == "measurements":
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
            bookings_qs = bookings_qs.filter(
                booking_status="upcoming"
            ).filter(has_dress_measurements).distinct()

        # Date filter
        if start_date and end_date:
            bookings_qs = bookings_qs.filter(
                pickup_date__date__range=(start_date, end_date)
            )
        elif start_date:
            bookings_qs = bookings_qs.filter(pickup_date__date__gte=start_date)
        elif end_date:
            bookings_qs = bookings_qs.filter(pickup_date__date__lte=end_date)

        # Purchase mode filter
        if purchase_mode_filter:
            bookings_qs = bookings_qs.filter(purchase_mode=purchase_mode_filter)

        # Search filter
        is_search_applied = False

        if search_query is not None:
            search_query = search_query.strip()

            if search_query:
                is_search_applied = True

                bookings_qs = bookings_qs.annotate(
                    search_rank=Case(

                        # ===== EXACT MATCH =====
                        When(client_identity__client_name__iexact=search_query, then=Value(0)),
                        When(staff_name__iexact=search_query, then=Value(0)),
                        When(staff__name__iexact=search_query, then=Value(0)),
                        When(shop_booking_id__iexact=search_query, then=Value(0)),
                        When(client_identity__phone_1_e164__iexact=search_query, then=Value(0)),

                        # ===== STARTS WITH =====
                        When(client_identity__client_name__istartswith=search_query, then=Value(1)),
                        When(staff_name__istartswith=search_query, then=Value(1)),
                        When(staff__name__istartswith=search_query, then=Value(1)),
                        When(shop_booking_id__istartswith=search_query, then=Value(1)),
                        When(client_identity__phone_1_e164__istartswith=search_query, then=Value(1)),

                        # ===== CONTAINS =====
                        default=Value(2),
                        output_field=IntegerField(),
                    )
                ).filter(
                    Q(client_identity__client_name__icontains=search_query)
                    | Q(staff_name__icontains=search_query)
                    | Q(staff__name__icontains=search_query)
                    | Q(shop_booking_id__icontains=search_query)
                    | Q(client_identity__phone_1_e164__icontains=search_query)
                )
        paginator = self.pagination_class()

        # Ordering
        if is_search_applied:
            bookings_qs = bookings_qs.order_by(
                "search_rank",
                "-id"
            )
        else:
            if status_filter == "all":
                # Every booking, latest created first.
                bookings_qs = bookings_qs.order_by("-created_at", "-id")
            elif status_filter in ["completed", "past"]:
                bookings_qs = bookings_qs.order_by("-pickup_date")
            else:
                bookings_qs = bookings_qs.order_by("pickup_date")

        result_page = paginator.paginate_queryset(bookings_qs, request)
        serializer = BookingListSerializer(result_page, many=True)
        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.error(f"Booking list error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching.",
            dev_message=str(e)
        )
