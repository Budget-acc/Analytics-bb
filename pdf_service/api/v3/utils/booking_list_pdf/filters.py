"""
Booking list filters for the Booking List PDF export.

Mirrors - line for line, including validation messages and ordering - the
two booking list APIs, so the PDF contains exactly the bookings the app
lists for the same filters:

- platform "mobile"  -> V4 list         booking_v2/api/v4/views/booking_viewset/list.py
- platform "desktop" -> V5 desktop-list booking_v2/api/v5/views/booking_viewset/desktop_list.py

The list views keep their filtering inline (mixed with pagination,
serialization and status counts), so it cannot be called from here without
changing them; this module copies it instead and the parity tests in
pdf_service/tests.py compare both against the live list endpoints. When a
list API's filters change, change the matching function here too.

`today` is passed in (resolved when the export is requested) so the
date-relative statuses give the same result the user saw, even if the PDF
task runs later. All filtering goes through the ORM (no raw SQL).
"""
from datetime import datetime

from django.db.models import Case, CharField, Exists, F, IntegerField, OuterRef, Prefetch, Q, Value, When
from django.db.models.functions import Lower

from booking_v2.models import BookedProductVariant, BookingV2

PLATFORM_MOBILE = "mobile"
PLATFORM_DESKTOP = "desktop"
PLATFORMS = (PLATFORM_MOBILE, PLATFORM_DESKTOP)

MOBILE_STATUSES = ("completed", "upcoming", "future", "past", "measurements", "all")
DESKTOP_STATUSES = ("completed", "upcoming", "returns", "pending", "not_returned", "cancelled", "expired", "all")
DESKTOP_SEARCH_MAX_LENGTH = 100

# Request-body keys accepted as filters (the same names as the list APIs' query params).
FILTER_KEYS = ("status", "search", "start_date", "end_date", "purchase_mode")


class BookingListFilterError(ValueError):
    """A filter value the mirrored list API would reject (same user message)."""

    def __init__(self, field, message, dev_message):
        super().__init__(message)
        self.field = field
        self.message = message
        self.dev_message = dev_message


def parse_custom_date(date_str):
    """Same parser as both list APIs: DD-MM-YYYY or YYYY-MM-DD, else None."""
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _apply_pickup_range(qs, start_date, end_date):
    if start_date and end_date:
        return qs.filter(pickup_date__date__range=(start_date, end_date))
    if start_date:
        return qs.filter(pickup_date__date__gte=start_date)
    if end_date:
        return qs.filter(pickup_date__date__lte=end_date)
    return qs


# ---------------------------------------------------------------------------
# Mobile - V4 BookingViewSet.list
# ---------------------------------------------------------------------------
def build_mobile_queryset(shop_id, params, today):
    status_filter = params.get("status")
    search_query = params.get("search", "")
    start_date_str = params.get("start_date")
    end_date_str = params.get("end_date")
    purchase_mode_filter = params.get("purchase_mode")

    start_date = parse_custom_date(start_date_str) if start_date_str else None
    end_date = parse_custom_date(end_date_str) if end_date_str else None

    if purchase_mode_filter:
        valid_purchase_modes = dict(BookingV2.PURCHASE_MODE_CHOICES)
        if purchase_mode_filter not in valid_purchase_modes:
            raise BookingListFilterError(
                "purchase_mode",
                "Invalid purchase_mode.",
                f"Invalid purchase_mode. Allowed values: {', '.join(valid_purchase_modes)}.",
            )

    qs = BookingV2.objects.filter(shop_id=shop_id)

    # Status filter ("all" and unknown values match no branch: no status filter)
    if status_filter == "completed":
        qs = qs.filter(booking_status="completed")
    elif status_filter == "upcoming":
        qs = qs.filter(booking_status="upcoming")
    elif status_filter == "future":
        qs = qs.filter(booking_status="upcoming").filter(return_date__date__gte=today)
    elif status_filter == "past":
        qs = qs.filter(booking_status="upcoming").filter(return_date__date__lt=today)
    elif status_filter == "measurements":
        has_dress_measurements = Exists(
            BookedProductVariant.objects.filter(
                booking_id=OuterRef("id"),
                deleted__isnull=True,
                measurements__isnull=False,
            ).exclude(
                Q(measurements={}) | Q(measurements=[])
            ).filter(
                product_variant__product__shop_service__general_service__main_category__name__icontains="dress"
            )
        )
        qs = qs.filter(booking_status="upcoming").filter(has_dress_measurements).distinct()

    qs = _apply_pickup_range(qs, start_date, end_date)

    if purchase_mode_filter:
        qs = qs.filter(purchase_mode=purchase_mode_filter)

    is_search_applied = False
    if search_query is not None:
        search_query = search_query.strip()
        if search_query:
            is_search_applied = True
            qs = qs.annotate(
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

    if is_search_applied:
        return qs.order_by("search_rank", "-id")
    if status_filter == "all":
        return qs.order_by("-created_at", "-id")
    if status_filter in ["completed", "past"]:
        return qs.order_by("-pickup_date")
    return qs.order_by("pickup_date")


# ---------------------------------------------------------------------------
# Desktop - V5 BookingViewSet.desktop_list
# ---------------------------------------------------------------------------
def build_desktop_queryset(shop_id, params, today):
    status_filter = (params.get("status") or "").strip().lower()
    search_query = (params.get("search") or "").strip()
    start_date_str = (params.get("start_date") or "").strip()
    end_date_str = (params.get("end_date") or "").strip()
    purchase_mode_filter = (params.get("purchase_mode") or "").strip().lower()

    start_date = end_date = None
    if start_date_str:
        start_date = parse_custom_date(start_date_str)
        if not start_date:
            raise BookingListFilterError(
                "start_date",
                "Invalid start_date format. Use DD-MM-YYYY or YYYY-MM-DD.",
                "start_date could not be parsed; send DD-MM-YYYY or YYYY-MM-DD.",
            )
    if end_date_str:
        end_date = parse_custom_date(end_date_str)
        if not end_date:
            raise BookingListFilterError(
                "end_date",
                "Invalid end_date format. Use DD-MM-YYYY or YYYY-MM-DD.",
                "end_date could not be parsed; send DD-MM-YYYY or YYYY-MM-DD.",
            )
    if start_date and end_date and start_date > end_date:
        raise BookingListFilterError(
            "start_date", "start_date cannot be after end_date.", "start_date is later than end_date.",
        )

    if status_filter and status_filter not in DESKTOP_STATUSES:
        raise BookingListFilterError(
            "status", "Invalid status filter.", f"status must be one of: {', '.join(DESKTOP_STATUSES)}.",
        )

    if purchase_mode_filter:
        valid_purchase_modes = dict(BookingV2.PURCHASE_MODE_CHOICES)
        if purchase_mode_filter not in valid_purchase_modes:
            raise BookingListFilterError(
                "purchase_mode",
                f"Invalid purchase_mode filter. Must be one of: {', '.join(valid_purchase_modes)}.",
                f"purchase_mode must be one of: {', '.join(valid_purchase_modes)}.",
            )

    qs = BookingV2.objects.filter(shop_id=shop_id)

    # "all" matches no branch below: no booking-status filter.
    if status_filter == "completed":
        qs = qs.filter(booking_status="completed").exclude(delivery_status="cancelled")
    elif status_filter == "upcoming":
        qs = qs.filter(booking_status="upcoming", pickup_date__date__gte=today).exclude(delivery_status="cancelled")
    elif status_filter == "returns":
        qs = qs.filter(
            booking_status="upcoming", pickup_date__date__lte=today, return_date__date__gte=today,
        ).exclude(Q(return_date__isnull=True) | Q(delivery_status="cancelled"))
    elif status_filter == "pending":
        qs = qs.filter(booking_status="upcoming", pickup_date__date__lt=today).exclude(delivery_status="cancelled")
    elif status_filter == "not_returned":
        qs = qs.filter(
            booking_status="upcoming", pickup_date__date__lt=today, return_date__date__lt=today,
        ).exclude(Q(return_date__isnull=True) | Q(delivery_status="cancelled") | Q(delivery_status="returned"))
    elif status_filter == "cancelled":
        qs = qs.filter(delivery_status="cancelled")
    elif status_filter == "expired":
        qs = qs.filter(booking_status="upcoming").filter(return_date__date__lt=today)

    qs = _apply_pickup_range(qs, start_date, end_date)

    if purchase_mode_filter:
        qs = qs.filter(purchase_mode=purchase_mode_filter)

    if len(search_query) > DESKTOP_SEARCH_MAX_LENGTH:
        raise BookingListFilterError(
            "search",
            "Search query too long. Maximum 100 characters.",
            f"search is {len(search_query)} characters; the maximum is {DESKTOP_SEARCH_MAX_LENGTH}.",
        )

    if search_query:
        qs = qs.annotate(
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
        return qs.order_by("search_rank", "sort_key", "-id")

    if status_filter in ("upcoming", "pending"):
        return qs.order_by("pickup_date", "created_at")
    if status_filter in ("returns", "not_returned"):
        return qs.order_by("return_date", "pickup_date")
    if status_filter in ("completed", "cancelled"):
        # Recently completed/cancelled first; completed_at may be null (nulls_last).
        return qs.order_by(F("completed_at").desc(nulls_last=True))
    if status_filter == "all":
        return qs.order_by("-created_at", "-id")
    return qs.order_by("pickup_date", "created_at")


_BUILDERS = {PLATFORM_MOBILE: build_mobile_queryset, PLATFORM_DESKTOP: build_desktop_queryset}


def build_queryset(platform, shop_id, params, today):
    """
    The selected list's bookings (filtered + ordered exactly like that list),
    loading only what the PDF prints: client name/phone in the same query and
    the booked product names in one prefetch query (no N+1).
    Raises BookingListFilterError for filters the list would reject.
    """
    builder = _BUILDERS.get(platform)
    if builder is None:
        raise BookingListFilterError(
            "platform", "Please choose mobile or desktop.", "platform must be one of: mobile, desktop.",
        )
    return builder(shop_id, params, today)


def with_pdf_columns(qs):
    """Narrow a filtered booking queryset to the columns printed in the PDF."""
    return qs.select_related("client_identity").prefetch_related(
        Prefetch(
            "booked_items",
            queryset=BookedProductVariant.objects.only("id", "booking_id", "product_name").order_by("id"),
            to_attr="pdf_items",
        )
    ).only(
        "id", "shop_booking_id", "pickup_date", "advance_amount", "total_amount", "discount_amount",
        "client_identity__client_name", "client_identity__phone_1_e164",
    )
