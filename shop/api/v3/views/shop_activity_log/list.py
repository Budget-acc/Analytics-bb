from django.db.models import Case, When, Value, IntegerField
from django.db.models.functions import Lower
from shop.models import ShopActivityLog
from ERP.utils.custom_pagination import StandardPageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging


logger = logging.getLogger(__name__)


@extend_schema(
    tags=["Shop Management V3"],
    summary="List shop activity logs",
    description=(
        "Returns a paginated list of activity logs (audit trail) for the active shop.\n\n"
        "Purpose: Lets the shop owner/manager see who did what and when - bookings, sales, "
        "payments, refunds, deletions, restores, etc.\n\n"
        "Context: Called from the Activity Log screen. The shop is taken from the authenticated "
        "user's active shop; no shop id is sent.\n\n"
        "Processing order: filters (action, q, user, content_type, start_date, end_date) are "
        "applied first; the ranked `search` then runs only on the already-filtered logs; the "
        "result is paginated. Without `search`, logs are ordered newest first.\n\n"
        "Accepted inputs (all query parameters, all optional):\n"
        "- action (string): one or more action types, comma-separated - e.g. deleted or deleted,updated.\n"
        "- search (string): ranked search on the log message.\n"
        "- q (string): plain contains-search on the log message.\n"
        "- user (string): user full name or phone (partial match).\n"
        "- content_type (string): model name, or app_label.model.\n"
        "- start_date / end_date (YYYY-MM-DD): created-date range, inclusive.\n"
        "- page (integer), page_size (integer): pagination.\n\n"
        "No path parameters or request body."
    ),
    parameters=[
        OpenApiParameter(
            "action", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Restricts the list to one or more action types. "
                "Location: query parameter. "
                "Format/Type: string - a single action or several separated by commas, e.g. "
                "`deleted` or `deleted,updated`. Examples of action types: created, updated, deleted, "
                "restored, cancelled, payment_updated, refund_added, refund_deleted, variant_deleted. "
                "Matching is case-insensitive; surrounding spaces and empty entries are ignored; "
                "duplicates are ignored; at most 20 values are used. An unknown action type simply "
                "returns no logs. "
                "Optional. If omitted (or empty), logs of all action types are returned."
            ),
        ),
        OpenApiParameter(
            "search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Ranked search on the log message, applied after all filters (including action). "
                "Results are ordered exact match first, then starts-with, then contains; alphabetical "
                "within each tier. "
                "Location: query parameter. "
                "Format/Type: string (case-insensitive). "
                "Optional. If omitted, logs are ordered newest first."
            ),
        ),
        OpenApiParameter(
            "q", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Plain filter - keeps logs whose message contains this text (no ranking). "
                "Location: query parameter. "
                "Format/Type: string (case-insensitive). "
                "Optional."
            ),
        ),
        OpenApiParameter(
            "user", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Keeps logs performed by users whose full name or phone contains this text. "
                "Location: query parameter. "
                "Format/Type: string (case-insensitive partial match). "
                "Optional."
            ),
        ),
        OpenApiParameter(
            "content_type", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Keeps logs for one kind of record. "
                "Location: query parameter. "
                "Format/Type: string - model name (e.g. bookingv2) or app_label.model "
                "(e.g. booking_v2.bookingv2); case-insensitive. "
                "Optional."
            ),
        ),
        OpenApiParameter(
            "start_date", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Keeps logs created on or after this date. "
                "Location: query parameter. "
                "Format/Type: date - YYYY-MM-DD (e.g. 2026-09-01). "
                "Optional."
            ),
        ),
        OpenApiParameter(
            "end_date", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Keeps logs created on or before this date. "
                "Location: query parameter. "
                "Format/Type: date - YYYY-MM-DD (e.g. 2026-09-30). "
                "Optional."
            ),
        ),
        OpenApiParameter(
            "page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Page number to return. "
                "Location: query parameter. "
                "Format/Type: integer >= 1. "
                "Optional. Default: 1."
            ),
        ),
        OpenApiParameter(
            "page_size", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False,
            description=(
                "Purpose: Number of logs per page. "
                "Location: query parameter. "
                "Format/Type: integer, maximum 100. "
                "Optional. Default: 15."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Returns a paginated list of activity logs.\n\n"
                "Response shape (under `data`):\n"
                "- next / previous (string | null): URLs of the next/previous page.\n"
                "- count (integer): total logs matching the filters and search.\n"
                "- page_size, current_page, total_pages, start_index, end_index (integer).\n"
                "- data (array): the logs on this page. Each log contains: id (integer), "
                "action (string), message (string), user (string - full name, or 'Unknown'), "
                "object_type (string | null), object_id (integer | null), "
                "created_at (string, DD-MM-YYYY HH:MM:SS, local time), "
                "is_restored (boolean for deleted logs - true if already restored; null otherwise), "
                "restored_from_activity_id (integer for restored logs; null otherwise), "
                "unique_code (string, only on variant_deleted logs when available)."
            )
        ),
        500: OpenApiResponse(
            description=(
                "Exact message: Unable to retrieve activity logs. Please try again later. "
                "(Also returned for an invalid start_date/end_date format.)"
            )
        ),
    },
    examples=[
        OpenApiExample(
            "Request example - multiple actions with search",
            value={"action": "deleted,updated", "search": "booking", "page": 1},
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Data retrieved successfully",
                "data": {
                    "next": None,
                    "previous": None,
                    "count": 2,
                    "page_size": 15,
                    "current_page": 1,
                    "total_pages": 1,
                    "start_index": 1,
                    "end_index": 2,
                    "data": [
                        {
                            "id": 120,
                            "action": "deleted",
                            "message": "Booking #BK-1001 deleted.",
                            "user": "Owner",
                            "object_type": "bookingv2",
                            "object_id": 1001,
                            "created_at": "25-09-2026 18:40:12",
                            "is_restored": False,
                            "restored_from_activity_id": None,
                        },
                        {
                            "id": 118,
                            "action": "updated",
                            "message": "Booking details were updated.",
                            "user": "Owner",
                            "object_type": "bookingv2",
                            "object_id": 998,
                            "created_at": "25-09-2026 17:02:45",
                            "is_restored": None,
                            "restored_from_activity_id": None,
                        },
                    ],
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
def list(self, request):
    try:
        # Base queryset with optimized select_related
        qs = (
            ShopActivityLog.objects
            .filter(shop_id=self.shop_id)
            .select_related("user", "content_type", "shop")
            .prefetch_related("restore_logs")
        )

        # Apply filters
        qs = self._apply_filters(qs, request)

        # Ranked search (separate from the plain `q` contains-filter above)
        search_query = (request.query_params.get("search") or "").strip()
        is_search_applied = False

        if search_query:
            is_search_applied = True
            qs = qs.annotate(
                search_rank=Case(
                    When(log_message__iexact=search_query, then=Value(0)),
                    When(log_message__istartswith=search_query, then=Value(1)),
                    When(log_message__icontains=search_query, then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                ),
                sort_key=Lower("log_message"),
            ).filter(log_message__icontains=search_query)

        if is_search_applied:
            qs = qs.order_by("search_rank", "sort_key", "-created_at")
        else:
            qs = qs.order_by("-created_at")

        # Paginate
        paginator = StandardPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)

        if not page:
            return paginator.get_paginated_response([])

        # Process logs with optimized data fetching
        result_items = self._process_logs(page)

        return paginator.get_paginated_response(result_items)

    except Exception as e:
        logger.error(f"Failed to list activity logs for shop {getattr(self, 'shop_id', None)}: {e}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve activity logs. Please try again later.",
            dev_message=str(e)
        )
