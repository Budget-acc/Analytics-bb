import logging
from collections import namedtuple

from django.db.models import CharField, Count, F, IntegerField, Prefetch, Q, Value, Case, When
from django.db.models.functions import Lower
from django.utils.timezone import localdate
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound

from booking_v2.api.v4.serializers.booking import BookingListSerializer
from booking_v2.models import BookingV2, BookedProductVariant
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation
from .desktop_list import parse_custom_date

logger = logging.getLogger("api_requests")

Msg = namedtuple("Msg", "message dev_message")

STATUS_KEYS = ("upcoming", "returns", "pending", "not_returned", "expired", "completed", "cancelled", "all")
PURCHASE_MODES = tuple(dict(BookingV2.PURCHASE_MODE_CHOICES))
MAX_SEARCH_LENGTH = 100

INVALID_STATUS = Msg("Please select a valid booking tab.", f"status must be one of: {', '.join(STATUS_KEYS)}.")
INVALID_PURCHASE_MODE = Msg(
    "Please select a valid purchase mode.", f"purchase_mode must be one of: {', '.join(PURCHASE_MODES)}.",
)
INVALID_START_DATE = Msg("Please enter a valid start date.", "start_date must be DD-MM-YYYY or YYYY-MM-DD.")
INVALID_END_DATE = Msg("Please enter a valid end date.", "end_date must be DD-MM-YYYY or YYYY-MM-DD.")
INVALID_DATE_RANGE = Msg("Start date cannot be after end date.", "start_date must be on or before end_date.")
SEARCH_TOO_LONG = Msg(
    f"Search text is too long. Please use up to {MAX_SEARCH_LENGTH} characters.",
    f"search must be at most {MAX_SEARCH_LENGTH} characters.",
)
SERVER_ERROR = Msg("Something went wrong while fetching bookings.", "Unexpected server error. Check server logs.")

# Ordering per tab when no search is applied (same as the desktop list).
STATUS_ORDERING = {
    "upcoming": ("pickup_date", "created_at"),
    "returns": ("return_date", "pickup_date"),
    "pending": ("pickup_date", "created_at"),
    "not_returned": ("return_date", "pickup_date"),
    "completed": (F("completed_at").desc(nulls_last=True),),
    "cancelled": (F("completed_at").desc(nulls_last=True),),
    "all": ("-created_at", "-id"),
}
DEFAULT_ORDERING = ("pickup_date", "created_at")


def status_filters(today):
    """Tab rules of the desktop booking list. One Q per tab drives both the
    list filter and the tab count, so a badge always matches its list."""
    upcoming = Q(booking_status="upcoming")
    not_cancelled = ~Q(delivery_status="cancelled")
    has_return = Q(return_date__isnull=False)
    return {
        "upcoming": upcoming & Q(pickup_date__date__gte=today) & not_cancelled,
        "returns": (
            upcoming & Q(pickup_date__date__lte=today, return_date__date__gte=today) & has_return & not_cancelled
        ),
        "pending": upcoming & Q(pickup_date__date__lt=today) & not_cancelled,
        "not_returned": (
            upcoming & Q(pickup_date__date__lt=today, return_date__date__lt=today) & has_return
            & not_cancelled & ~Q(delivery_status="returned")
        ),
        "expired": upcoming & Q(return_date__date__lt=today),
        "completed": Q(booking_status="completed") & not_cancelled,
        "cancelled": Q(delivery_status="cancelled"),
        "all": Q(),
    }


def _search(queryset, term):
    """Ranked search of the desktop list: booking id, then client name,
    staff name and client phone; exact > starts with > contains."""
    fields = (
        "shop_booking_id",
        "client_identity__client_name",
        "staff__name",
        "client_identity__phone_1_e164",
    )
    rank_cases, key_cases, matches = [], [], Q()
    for index, field in enumerate(fields):
        base = index * 3
        rank_cases += [
            When(**{f"{field}__iexact": term}, then=Value(base)),
            When(**{f"{field}__istartswith": term}, then=Value(base + 1)),
            When(**{f"{field}__icontains": term}, then=Value(base + 2)),
        ]
        key_cases.append(When(search_rank__in=[base, base + 1, base + 2], then=Lower(field)))
        matches |= Q(**{f"{field}__icontains": term})
    return (
        queryset
        .annotate(search_rank=Case(*rank_cases, default=Value(len(fields) * 3), output_field=IntegerField()))
        .filter(matches)
        .annotate(sort_key=Case(*key_cases, default=Value(""), output_field=CharField()))
        .order_by("search_rank", "sort_key", "-id")
    )


def _query_param(request, name):
    return (request.query_params.get(name) or "").strip()


SUCCESS_EXAMPLE = {
    "status": "success",
    "message": "Data retrieved successfully",
    "data": {
        "next": "https://api.example.com/api/v5/bookings/bookings/?page=2&page_size=15&status=returns",
        "previous": None, "count": 18, "page_size": 15, "current_page": 1,
        "total_pages": 2, "start_index": 1, "end_index": 15,
        "data": [{
            "id": 1042, "shop_booking_id": "BK01042", "client": "Anjali Menon",
            "pickup_date": "2026-10-27T10:00:00+05:30", "return_date": "2026-10-30T18:00:00+05:30",
            "cooling_period_end": "2026-10-31T18:00:00+05:30", "booking_status": "upcoming",
            "payment_status": False, "staff_name": "Rahul", "staff_color": "#4F46E5",
            "delivery_status": "delivered", "created_at": "2026-10-20T14:12:33+05:30",
            "booked_items": ["Wedding Gown", "Bridal Veil"],
        }],
        "status_counts": {
            "upcoming": 42, "returns": 18, "pending": 25, "not_returned": 7,
            "expired": 9, "completed": 310, "cancelled": 12, "all": 391,
        },
    },
}


def _error_example(name, msg, status_code, status_value="validation_error"):
    return OpenApiExample(
        name,
        value={"status": status_value, "message": msg.message, "data": None, "dev_message": msg.dev_message},
        response_only=True, status_codes=[str(status_code)],
    )


@extend_schema(
    tags=["Booking Management V5"],
    summary="List bookings by tab with tab counts (mobile)",
    description=(
        "Returns the active shop's bookings for one booking tab, in the mobile booking list format, together "
        "with the total number of bookings in every tab.\n\n"
        "Purpose: Powers the tabbed booking screen of the mobile app - Upcoming, Returns, Pending, Not Returned, "
        "Expired / Past, Completed, Cancelled and All - in one call: the selected tab's page of bookings plus a "
        "count badge for each tab.\n\n"
        "Context: Booking items have the same fields as GET /api/v4/bookings/bookings/ (mobile list). Tabs, "
        "filters, validation and ordering follow GET /api/v5/bookings/bookings/desktop-list/. Tab rules "
        "(today = server local date; 'upcoming booking' = booking_status upcoming):\n"
        "- upcoming: upcoming booking, pickup date >= today, not cancelled.\n"
        "- returns: upcoming booking, pickup date <= today <= return date, not cancelled.\n"
        "- pending: upcoming booking, pickup date < today, not cancelled.\n"
        "- not_returned: upcoming booking, pickup and return dates < today, not cancelled or returned.\n"
        "- expired: upcoming booking, return date < today.\n"
        "- completed: booking_status completed, not cancelled.\n"
        "- cancelled: delivery_status cancelled.\n"
        "- all: every booking.\n"
        "data.status_counts uses the same rules and ignores search, start_date, end_date and purchase_mode, so "
        "the badges stay the same while the user searches. Read-only; works while the shop subscription is "
        "inactive.\n\n"
        "Accepted inputs:\n"
        "Headers - Required:\n"
        "- X-Active-Shop-ID (integer): the shop whose bookings are listed.\n\n"
        "Query parameters - Optional:\n"
        "- status (string): upcoming, returns, pending, not_returned, expired, completed, cancelled or all. "
        "Default: no tab filter, ordered by pickup date.\n"
        "- search (string, at most 100 characters): booking id, client name, staff name or client phone.\n"
        "- start_date (DD-MM-YYYY or YYYY-MM-DD): earliest pickup date, inclusive.\n"
        "- end_date (DD-MM-YYYY or YYYY-MM-DD): latest pickup date, inclusive. Must be >= start_date.\n"
        "- purchase_mode (string): normal, package, courier or pickup.\n"
        "- page (integer >= 1): page number. Default: 1.\n"
        "- page_size (integer 1-100): bookings per page. Default: 15."
    ),
    parameters=[
        OpenApiParameter(
            name="X-Active-Shop-ID", type=OpenApiTypes.INT, location=OpenApiParameter.HEADER, required=True,
            description=(
                "Purpose: Selects the shop whose bookings are listed; the user must have access to it. "
                "Location: request header. Format/Type: integer shop id (e.g. 12). Required."
            ),
        ),
        OpenApiParameter(
            name="status", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            enum=[*STATUS_KEYS],
            description=(
                "Purpose: Selects the booking tab to list (rules in the description). "
                "Location: query parameter. Format/Type: string, case-insensitive; one of upcoming, returns, "
                "pending, not_returned, expired, completed, cancelled, all. "
                "Optional. Default: no tab filter, ordered by pickup date."
            ),
        ),
        OpenApiParameter(
            name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Finds bookings by booking id, client name, staff name or client phone (partial, "
                "case-insensitive). Results are ranked: booking id matches first, exact before starts-with "
                "before contains. Location: query parameter. Format/Type: string, at most 100 characters. "
                "Optional. Blank is ignored."
            ),
        ),
        OpenApiParameter(
            name="start_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Lists only bookings picked up on or after this date. "
                "Location: query parameter. Format/Type: date string, DD-MM-YYYY or YYYY-MM-DD "
                "(e.g. 01-10-2026). Optional. No lower bound if omitted."
            ),
        ),
        OpenApiParameter(
            name="end_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Lists only bookings picked up on or before this date. Must not be before start_date. "
                "Location: query parameter. Format/Type: date string, DD-MM-YYYY or YYYY-MM-DD "
                "(e.g. 2026-10-31). Optional. No upper bound if omitted."
            ),
        ),
        OpenApiParameter(
            name="purchase_mode", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False,
            enum=[*PURCHASE_MODES],
            description=(
                "Purpose: Lists only bookings with this purchase mode. "
                "Location: query parameter. Format/Type: string, case-insensitive; one of normal, package, "
                "courier, pickup. Optional. All purchase modes if omitted."
            ),
        ),
        OpenApiParameter(
            name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description="Purpose: Page number. Location: query parameter. Format/Type: integer >= 1. Optional. Default: 1.",
        ),
        OpenApiParameter(
            name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Number of bookings per page. Location: query parameter. Format/Type: integer 1-100 "
                "(larger values are capped at 100). Optional. Default: 15."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description=(
            "Success. Page-number paginated list. Exact message: Data retrieved successfully\n\n"
            "Response shape:\n"
            "- data.count, data.page_size, data.current_page, data.total_pages, data.start_index, "
            "data.end_index (integers); data.next, data.previous (string | null): pagination of the selected tab.\n"
            "- data.data (array of bookings). Each booking contains: id (integer), shop_booking_id "
            "(string | null), client (string | null), pickup_date, return_date, cooling_period_end "
            "(ISO 8601 datetime | null), booking_status (upcoming | completed), payment_status (boolean), "
            "staff_name, staff_color (string | null), delivery_status (string | null), created_at (ISO 8601 "
            "datetime), booked_items (array of product names).\n"
            "- data.status_counts (object of integers): upcoming, returns, pending, not_returned, expired, "
            "completed, cancelled, all - total bookings in each tab, unaffected by search, dates and "
            "purchase_mode.\n"
            "An empty data.data means no booking matches; status_counts is still returned."
        )),
        400: OpenApiResponse(description=(
            "Validation error. Exact messages:\n"
            f"- {INVALID_STATUS.message} (status is not one of the tabs)\n"
            f"- {INVALID_PURCHASE_MODE.message} (purchase_mode is not normal, package, courier or pickup)\n"
            f"- {INVALID_START_DATE.message} (start_date is not DD-MM-YYYY or YYYY-MM-DD)\n"
            f"- {INVALID_END_DATE.message} (end_date is not DD-MM-YYYY or YYYY-MM-DD)\n"
            f"- {INVALID_DATE_RANGE.message}\n"
            f"- {SEARCH_TOO_LONG.message}"
        )),
        401: OpenApiResponse(description=(
            "Authentication error (DRF `detail` response). Exact messages include:\n"
            "- Authentication credentials were not provided.\n"
            "- Given token not valid for any token type\n"
            "- Session invalidated\n"
            "- Token invalidated by password change"
        )),
        403: OpenApiResponse(description=(
            "The X-Active-Shop-ID header is missing or invalid, or the user cannot access the shop "
            "(DRF `detail` response). Exact message: You do not have permission to perform this action."
        )),
        404: OpenApiResponse(description=(
            "Page past the last page (DRF `detail` response). Exact message: Invalid page."
        )),
        500: OpenApiResponse(description=f"Unexpected error. Exact message: {SERVER_ERROR.message}"),
    },
    examples=[
        OpenApiExample("Success", value=SUCCESS_EXAMPLE, response_only=True, status_codes=["200"]),
        _error_example("Invalid status", INVALID_STATUS, 400),
        _error_example("Invalid date range", INVALID_DATE_RANGE, 400),
        _error_example("Server error", SERVER_ERROR, 500, status_value="error"),
    ],
)
@skip_subscription_validation
def list(self, request):
    status_filter = _query_param(request, "status").lower()
    purchase_mode = _query_param(request, "purchase_mode").lower()
    search = _query_param(request, "search")
    start_date_str = _query_param(request, "start_date")
    end_date_str = _query_param(request, "end_date")

    def invalid(field, msg):
        return self.validation_error_response(
            errors={field: [msg.message]}, message=msg.message, dev_message=msg.dev_message,
        )

    if status_filter and status_filter not in STATUS_KEYS:
        return invalid("status", INVALID_STATUS)
    if purchase_mode and purchase_mode not in PURCHASE_MODES:
        return invalid("purchase_mode", INVALID_PURCHASE_MODE)
    if len(search) > MAX_SEARCH_LENGTH:
        return invalid("search", SEARCH_TOO_LONG)
    start_date = parse_custom_date(start_date_str) if start_date_str else None
    if start_date_str and start_date is None:
        return invalid("start_date", INVALID_START_DATE)
    end_date = parse_custom_date(end_date_str) if end_date_str else None
    if end_date_str and end_date is None:
        return invalid("end_date", INVALID_END_DATE)
    if start_date and end_date and start_date > end_date:
        return invalid("end_date", INVALID_DATE_RANGE)

    try:
        filters = status_filters(localdate())
        shop_bookings = BookingV2.objects.filter(shop_id=self.shop_id)

        # One aggregate query for all eight tab badges.
        status_counts = shop_bookings.aggregate(
            **{key: Count("id", filter=rule) for key, rule in filters.items()}
        )

        bookings = shop_bookings.select_related("client_identity", "staff").prefetch_related(
            # No to_attr: the v4 serializer reads obj.booked_items.all(),
            # which is served from this prefetch cache.
            Prefetch("booked_items", queryset=BookedProductVariant.objects.only("id", "booking_id", "product_name"))
        )
        if status_filter:
            bookings = bookings.filter(filters[status_filter])
        if start_date:
            bookings = bookings.filter(pickup_date__date__gte=start_date)
        if end_date:
            bookings = bookings.filter(pickup_date__date__lte=end_date)
        if purchase_mode:
            bookings = bookings.filter(purchase_mode=purchase_mode)

        if search:
            bookings = _search(bookings, search)
        else:
            bookings = bookings.order_by(*STATUS_ORDERING.get(status_filter, DEFAULT_ORDERING))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(bookings, request, view=self)
        response = paginator.get_paginated_response(BookingListSerializer(page, many=True).data)
        response.data["data"]["status_counts"] = status_counts
        return response
    except NotFound:
        raise
    except Exception:
        logger.exception("Mobile booking list failed: shop=%s status=%s", self.shop_id, status_filter)
        return self.error_response(
            message=SERVER_ERROR.message,
            dev_message=SERVER_ERROR.dev_message,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


__all__ = ["list"]
