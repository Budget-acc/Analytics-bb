import logging
from django.db.models import F, Q, Prefetch, Case, When, Value, IntegerField, CharField
from django.db.models.functions import Lower
from django.utils.timezone import localdate
from rest_framework.decorators import action
from shop.models import Shop
from booking_v2.models import BookingV2, BookedProductVariant
from booking_v2.api.v5.serializers import BookingDesktopListSerializer
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


def parse_custom_date(date_str):
    from datetime import datetime
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


@extend_schema(
    tags=["Booking Management V5"],
    summary="Desktop booking list",
    description="Returns desktop-oriented booking list with status counts and filters.",
    parameters=[
        OpenApiParameter(name="status", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Status filter. Allowed: completed, upcoming, returns, pending, not_returned, cancelled, expired, all (all = every booking, newest created first)."),
        OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Search by client/invoice/product."),
        OpenApiParameter(name="start_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Date format DD-MM-YYYY or YYYY-MM-DD."),
        OpenApiParameter(name="end_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Date format DD-MM-YYYY or YYYY-MM-DD."),
        OpenApiParameter(name="purchase_mode", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Filters bookings by purchase mode. Allowed values: normal, package, courier, pickup."),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False),
        OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False),
    ],
    responses={
        200: OpenApiResponse(description="Success response with status counts and booking list."),
        400: OpenApiResponse(description="Exact messages include: Shop not found.; Invalid start_date format. Use DD-MM-YYYY or YYYY-MM-DD.; Invalid end_date format. Use DD-MM-YYYY or YYYY-MM-DD.; start_date cannot be after end_date.; Invalid purchase_mode filter.; Search query too long. Maximum 100 characters.; We couldn't load this page of bookings. Please go back to the first page and try again.; Error formatting booking data."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching bookings."),
    },
)
@skip_subscription_validation
@action(detail=False, methods=["get"], url_path="desktop-list")
def desktop_list(self, request):
    """
    Optimized desktop booking list with status filters:
    - completed: booking_status == 'completed' (excludes cancelled)
    - upcoming: pickup_date >= today AND booking_status == 'upcoming' (excludes cancelled)
    - returns: pickup_date < today AND return_date >= today AND booking_status == 'upcoming' (excludes cancelled)
    - pending: pickup_date < today AND booking_status == 'upcoming' (excludes cancelled)
    - not_returned: pickup_date < today AND return_date < today AND booking_status == 'upcoming' (excludes cancelled)
    - cancelled: delivery_status == 'cancelled'
    - expired: return_date < today AND booking_status == 'upcoming'
    - all: every booking (no status filter), latest created first

    Response includes status_counts independent of filters for quick navigation.
    """
    try:
        shop_id = self.shop_id

        # Validate shop exists (prevents SQL injection via shop_id)
        try:
            shop = Shop.objects.only('id').get(id=shop_id)
        except Shop.DoesNotExist:
            return self.error_response(
                message="Shop not found.",
                dev_message=f"Shop with id {shop_id} does not exist"
            )

        # Get today's date in local timezone (needed for status counts)
        today = localdate()

        # Calculate status counts (independent of all filters)
        try:
            base_count_qs = BookingV2.objects.filter(shop_id=shop_id)

            status_counts = {
                "completed": base_count_qs.filter(
                    booking_status="completed"
                ).count(),

                "upcoming": base_count_qs.filter(
                    booking_status="upcoming",
                    pickup_date__date__gte=today
                ).exclude(
                    delivery_status="cancelled"
                ).count(),

                "returns": base_count_qs.filter(
                    booking_status="upcoming",
                    pickup_date__date__lte=today,
                    return_date__date__gte=today
                ).exclude(
                    Q(return_date__isnull=True) | Q(delivery_status="cancelled")
                ).count(),

                "pending": base_count_qs.filter(
                    booking_status="upcoming",
                    pickup_date__date__lt=today
                ).exclude(
                    delivery_status="cancelled"
                ).count(),

                "not_returned": base_count_qs.filter(
                    booking_status="upcoming",
                    pickup_date__date__lt=today,
                    return_date__date__lt=today
                ).exclude(
                    Q(return_date__isnull=True) | Q(delivery_status="cancelled") | Q(delivery_status="returned")
                ).count(),

                "cancelled": base_count_qs.filter(
                    delivery_status="cancelled"
                ).count(),

                "expired": base_count_qs.filter(
                    booking_status="upcoming",
                    return_date__date__lt=today
                ).count(),

                "all": base_count_qs.count(),
            }
        except Exception as count_err:
            logger.error(f"Status count calculation error: {str(count_err)}", exc_info=True)
            status_counts = {
                "completed": 0,
                "upcoming": 0,
                "returns": 0,
                "pending": 0,
                "not_returned": 0,
                "cancelled": 0,
                "expired": 0,
                "all": 0,
            }

        # Get query parameters with validation
        status_filter = request.query_params.get("status", "").strip().lower()
        search_query = request.query_params.get("search", "").strip()
        start_date_str = request.query_params.get("start_date", "").strip()
        end_date_str = request.query_params.get("end_date", "").strip()
        purchase_mode_filter = (request.query_params.get("purchase_mode") or "").strip().lower()

        # Parse dates with null safety
        start_date = None
        end_date = None
        if start_date_str:
            start_date = parse_custom_date(start_date_str)
            if not start_date:
                return self.error_response(
                    message="Invalid start_date format. Use DD-MM-YYYY or YYYY-MM-DD.",
                    dev_message=f"Failed to parse: {start_date_str}"
                )

        if end_date_str:
            end_date = parse_custom_date(end_date_str)
            if not end_date:
                return self.error_response(
                    message="Invalid end_date format. Use DD-MM-YYYY or YYYY-MM-DD.",
                    dev_message=f"Failed to parse: {end_date_str}"
                )

        # Validate date range
        if start_date and end_date and start_date > end_date:
            return self.error_response(
                message="start_date cannot be after end_date.",
                dev_message=f"start_date: {start_date}, end_date: {end_date}"
            )

        # Base queryset with optimized select_related and prefetch_related
        bookings_qs = BookingV2.objects.filter(shop_id=shop_id).select_related(
            "client_identity",
            "staff"
        ).prefetch_related(
            Prefetch(
                "booked_items",
                queryset=BookedProductVariant.objects.only(
                    "id", "booking_id", "product_name"
                ),
                to_attr="booked_items_list"
            )
        ).only(
            "id",
            "shop_booking_id",
            "pickup_date",
            "return_date",
            "payment_status",
            "delivery_status",
            "created_at",
            "advance_amount",
            "total_amount",
            "discount_amount",
            "booking_status",
            "client_identity_id",
            "staff_id",
            "completed_at"
        )

        # Status filter with validated choices
        valid_statuses = ["completed", "upcoming", "returns", "pending", "not_returned", "cancelled", "expired", "all"]

        if status_filter and status_filter not in valid_statuses:
            return self.error_response(
                message=f"Invalid status filter.",
                dev_message=f"Received: {status_filter}. Must be one of: {', '.join(valid_statuses)}"
            )

        # Purchase mode filter validation
        if purchase_mode_filter:
            valid_purchase_modes = dict(BookingV2.PURCHASE_MODE_CHOICES)
            if purchase_mode_filter not in valid_purchase_modes:
                return self.error_response(
                    message=f"Invalid purchase_mode filter. Must be one of: {', '.join(valid_purchase_modes)}.",
                    dev_message=f"Received: {purchase_mode_filter}"
                )

        # "all" matches no branch below: no booking-status filter.
        if status_filter == "completed":
            bookings_qs = bookings_qs.filter(
                booking_status="completed"
            ).exclude(
                delivery_status="cancelled"
            )

        elif status_filter == "upcoming":
            bookings_qs = bookings_qs.filter(
                booking_status="upcoming",
                pickup_date__date__gte=today
            ).exclude(
                delivery_status="cancelled"
            )

        elif status_filter == "returns":
            bookings_qs = bookings_qs.filter(
                booking_status="upcoming",
                pickup_date__date__lte=today,
                return_date__date__gte=today
            ).exclude(
                Q(return_date__isnull=True) | Q(delivery_status="cancelled")
            )

        elif status_filter == "pending":
            bookings_qs = bookings_qs.filter(
                booking_status="upcoming",
                pickup_date__date__lt=today
            ).exclude(
                delivery_status="cancelled"
            )

        elif status_filter == "not_returned":
            bookings_qs = bookings_qs.filter(
                booking_status="upcoming",
                pickup_date__date__lt=today,
                return_date__date__lt=today
            ).exclude(
                Q(return_date__isnull=True) | Q(delivery_status="cancelled") | Q(delivery_status="returned")
            )

        elif status_filter == "cancelled":
            bookings_qs = bookings_qs.filter(
                delivery_status="cancelled"
            )

        elif status_filter == "expired":
            bookings_qs = bookings_qs.filter(
                booking_status="upcoming"
            ).filter(
                return_date__date__lt=today
            )

        # Date range filter (applied to pickup_date)
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

            # DOS protection
            if len(search_query) > 100:
                return self.error_response(
                    message="Search query too long. Maximum 100 characters.",
                    dev_message=f"Query length: {len(search_query)}"
                )

            if search_query:
                is_search_applied = True

                bookings_qs = bookings_qs.annotate(
                    search_rank=Case(
                        # ===== SHOP BOOKING ID (highest field priority) =====
                        When(shop_booking_id__iexact=search_query, then=Value(0)),
                        When(shop_booking_id__istartswith=search_query, then=Value(1)),
                        When(shop_booking_id__icontains=search_query, then=Value(2)),
                        # ===== CLIENT NAME =====
                        When(client_identity__client_name__iexact=search_query, then=Value(3)),
                        When(client_identity__client_name__istartswith=search_query, then=Value(4)),
                        When(client_identity__client_name__icontains=search_query, then=Value(5)),
                        # ===== STAFF NAME =====
                        When(staff__name__iexact=search_query, then=Value(6)),
                        When(staff__name__istartswith=search_query, then=Value(7)),
                        When(staff__name__icontains=search_query, then=Value(8)),
                        # ===== CLIENT PHONE NUMBER =====
                        When(client_identity__phone_1_e164__iexact=search_query, then=Value(9)),
                        When(client_identity__phone_1_e164__istartswith=search_query, then=Value(10)),
                        When(client_identity__phone_1_e164__icontains=search_query, then=Value(11)),

                        default=Value(12),
                        output_field=IntegerField(),
                    )
                ).filter(
                    Q(client_identity__client_name__icontains=search_query)
                    | Q(staff__name__icontains=search_query)
                    | Q(shop_booking_id__icontains=search_query)
                    | Q(client_identity__phone_1_e164__icontains=search_query)
                ).annotate(
                    sort_key=Case(
                        When(search_rank__in=[0, 1, 2], then=Lower("shop_booking_id")),
                        When(search_rank__in=[3, 4, 5], then=Lower("client_identity__client_name")),
                        When(search_rank__in=[6, 7, 8], then=Lower("staff__name")),
                        When(search_rank__in=[9, 10, 11], then=Lower("client_identity__phone_1_e164")),
                        default=Value(""),
                        output_field=CharField(),
                    )
                )

        # ---------- Ordering ----------
        if is_search_applied:
            bookings_qs = bookings_qs.order_by("search_rank", "sort_key", "-id")
        else:
            if status_filter == "upcoming":
                bookings_qs = bookings_qs.order_by("pickup_date", "created_at")
            elif status_filter == "returns":
                bookings_qs = bookings_qs.order_by("return_date", "pickup_date")
            elif status_filter == "completed":
                # Recently completed first. nulls_last so any legacy
                # completed booking predating completed_at doesn't outrank
                # actually-recent completions.
                bookings_qs = bookings_qs.order_by(F("completed_at").desc(nulls_last=True))
            elif status_filter == "pending":
                bookings_qs = bookings_qs.order_by("pickup_date", "created_at")
            elif status_filter == "not_returned":
                # Oldest return date first (most overdue first).
                bookings_qs = bookings_qs.order_by("return_date", "pickup_date")
            elif status_filter == "cancelled":
                # Recently cancelled first. delivery_status can be set to
                # cancelled independently of running through the completion
                # action, so completed_at may be null for some cancelled
                # rows - nulls_last keeps those at the end instead of
                # incorrectly sorting first.
                bookings_qs = bookings_qs.order_by(F("completed_at").desc(nulls_last=True))
            elif status_filter == "all":
                # Every booking (no status filter), latest created first.
                bookings_qs = bookings_qs.order_by("-created_at", "-id")
            else:
                bookings_qs = bookings_qs.order_by("pickup_date", "created_at")

        # Pagination with error handling
        try:
            paginator = self.pagination_class()
            result_page = paginator.paginate_queryset(bookings_qs, request)
        except Exception as page_err:
            logger.error(f"Pagination error: {str(page_err)}", exc_info=True)
            return self.error_response(
                message="We couldn't load this page of bookings. Please go back to the first page and try again.",
                dev_message=str(page_err)
            )

        # Serialize with error handling
        try:
            serializer = BookingDesktopListSerializer(result_page, many=True)

            # Get paginated response
            response = paginator.get_paginated_response(serializer.data)

            # Add status counts to response data
            response.data['data']['status_counts'] = status_counts

            return response
        except Exception as ser_err:
            logger.error(f"Serialization error: {str(ser_err)}", exc_info=True)
            return self.error_response(
                message="Error formatting booking data.",
                dev_message=str(ser_err)
            )

    except Exception as e:
        logger.error(f"Desktop booking list error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching bookings.",
            dev_message=str(e)
        )


__all__ = ["desktop_list"]
