from collections import defaultdict, OrderedDict
from datetime import datetime, date
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from django.db.models import F, Value, Prefetch, Q, Exists, OuterRef, Case, When, IntegerField, Count
from django.db.models.functions import Coalesce, Length, Lower
from django.utils.timezone import localtime, localdate, make_aware, now
from ERP.utils.response_utils import ResponseStatus, ResponseMessage
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.pagination import PageNumberPagination
from ERP.utils.mixins import StandardResponseMixin
from rest_framework.views import APIView
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import BookingV2, Client, BookedProductVariant, PaymentDetailsV2, ClientRequirementV2
from ERP.utils.custom_pagination import StandardPageNumberPagination
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from shop.models import Shop
from safedelete.models import HARD_DELETE
from django.db import transaction
from booking_v2.api.v4.serializers.booking import BookingListSerializer, BookingDetailSerializer, ClientRequirementV2Serializer
from booking_v2.api.v4.serializers.product import BookedProductVariantSerializer
from services.models import ProductVariant
from booking_v2.api.v4.utils.availability import check_product_variant_availability, add_product_variant_availability, remove_product_variant_availability
from pytz import timezone as pytz_timezone
import pytz
from django.utils import timezone
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from itertools import groupby
from tailored.models import TailorOrderItem
from phonenumber_field.phonenumber import PhoneNumber
from phonenumbers import NumberParseException
import phonenumbers
from rest_framework.exceptions import ValidationError, ParseError
import logging
from ERP.utils.shop_activity_logs import create_activity_log
from ERP.utils.utils import validate_and_format_phone
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")

india = pytz_timezone('Asia/Kolkata')


def parse_custom_date(date_str):
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


@extend_schema(
    tags=["Booking Management V4"],
    summary="List bookings",
    description=(
        "Returns paginated bookings for the active shop with optional status, date range, and search filters.\n\n"
        "Purpose: Primary listing API for the booking management screen.\n\n"
        "Context: Supports operational status filtering and ranked search for quick client/staff lookup."
    ),
    parameters=[
        OpenApiParameter(
            name="status",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Filters bookings by lifecycle stage. "
                "Location: query parameter. "
                "Format/Type: string. "
                "Allowed values: completed, upcoming, future, past, measurements, all "
                "(all = every booking, no status filter, newest created first). "
                "Optional."
            ),
        ),
        OpenApiParameter(
            name="search",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Free-text ranked search across client name, staff name, and shop_booking_id. "
                "Location: query parameter. "
                "Format/Type: string. "
                "Optional."
            ),
        ),
        OpenApiParameter(
            name="start_date",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Lower bound (inclusive) for pickup date filter. "
                "Location: query parameter. "
                "Format/Type: string; accepted formats: DD-MM-YYYY or YYYY-MM-DD. "
                "Optional."
            ),
        ),
        OpenApiParameter(
            name="end_date",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Upper bound (inclusive) for pickup date filter. "
                "Location: query parameter. "
                "Format/Type: string; accepted formats: DD-MM-YYYY or YYYY-MM-DD. "
                "Optional."
            ),
        ),
        OpenApiParameter(
            name="purchase_mode",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Filters bookings by purchase mode. "
                "Location: query parameter. "
                "Format/Type: string. "
                "Allowed values: normal, package, courier, pickup. "
                "Optional."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(description="Paginated success response with booking list payload."),
        500: OpenApiResponse(description="Server error.\n\nExact message:\n- Something went wrong while fetching."),
    },
)
@skip_subscription_validation
def list(self, request):
    try:
        # shop = request.user.shop_memberships.select_related("shop").first().shop
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
            "client_identity", "service", "staff"
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
