import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import localdate
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from pdf_service.api.v3.utils.booking_list_pdf import PLATFORMS, BookingListFilterError, build_queryset
from pdf_service.api.v3.utils.booking_list_pdf.filters import FILTER_KEYS
from pdf_service.models import PDFTask
from pdf_service.tasks import compute_request_hash, generate_booking_list_pdf_task
from shop.api.admin_v1.utils.shop import get_active_shop_id
from shop.models import Shop
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from .throttles import PDFGenerationThrottle

logger = logging.getLogger("api_requests")

PDF_TYPE = "booking_list"
REUSE_WINDOW = timedelta(minutes=15)

MSG_STARTED = "Booking PDF generation started."
MSG_REUSED = "Your booking PDF is already being prepared."
MSG_PLATFORM = "Please choose mobile or desktop."
MSG_INVALID_BODY = "Please provide valid filters."
MSG_TOO_MANY = "Too many bookings to export. Please narrow the filters (maximum {limit:,})."
MSG_QUEUE_FAILED = "We couldn't start the PDF right now. Please try again."
MSG_SERVER_ERROR = "Something went wrong while starting the booking PDF. Please try again."
DEV_POLL = "Poll GET /api/v3/pdf/status/{task_id}/ until status is completed, then use download_url."


def _max_rows() -> int:
    return int(getattr(settings, "BOOKING_LIST_PDF_MAX_ROWS", 5000))


def _filter_field_help(purpose, mobile, desktop):
    return (
        f"Purpose: {purpose} "
        "Location: request body. "
        f"Format/Type: string. Mobile: {mobile} Desktop: {desktop} "
        "Optional."
    )


REQUEST_SERIALIZER = inline_serializer(
    name="PDFV3BookingListExportRequest",
    fields={
        "platform": serializers.ChoiceField(
            choices=list(PLATFORMS),
            help_text=(
                "Purpose: Which booking list's filters to apply - the Mobile list (V4 bookings list) or the "
                "Desktop list (V5 desktop-list). The PDF contains exactly the bookings that list shows. "
                "Location: request body. Format/Type: string, one of: mobile, desktop. Required."
            ),
        ),
        "status": serializers.CharField(required=False, allow_null=True, help_text=_filter_field_help(
            "Booking status filter, same as the list's `status` query parameter.",
            "completed, upcoming, future, past, measurements, all (exact value; any other value = no status filter).",
            "completed, upcoming, returns, pending, not_returned, cancelled, expired, all (case-insensitive; "
            "any other value is rejected).",
        )),
        "search": serializers.CharField(required=False, allow_null=True, help_text=_filter_field_help(
            "Free-text search, same as the list's `search` query parameter (client name, staff name, "
            "invoice id, client phone).",
            "any length.",
            "maximum 100 characters.",
        )),
        "start_date": serializers.CharField(required=False, allow_null=True, help_text=_filter_field_help(
            "Lower bound (inclusive) of the pickup date.",
            "DD-MM-YYYY or YYYY-MM-DD; an unparseable value is ignored (same as the list).",
            "DD-MM-YYYY or YYYY-MM-DD; an unparseable value is rejected.",
        )),
        "end_date": serializers.CharField(required=False, allow_null=True, help_text=_filter_field_help(
            "Upper bound (inclusive) of the pickup date.",
            "DD-MM-YYYY or YYYY-MM-DD; an unparseable value is ignored (same as the list).",
            "DD-MM-YYYY or YYYY-MM-DD; an unparseable value or a start_date after end_date is rejected.",
        )),
        "purchase_mode": serializers.CharField(required=False, allow_null=True, help_text=_filter_field_help(
            "Purchase mode filter.",
            "normal, package, courier, pickup (exact value).",
            "normal, package, courier, pickup (case-insensitive).",
        )),
    },
)


class ExportBookingListPDFView(StandardResponseMixin, APIView):
    """Booking List PDF export for the Mobile and Desktop booking lists (async, tracked by PDFTask)."""
    authentication_classes = [CutoffJWTAuthentication]
    throttle_classes = [PDFGenerationThrottle]
    permission_classes = [IsAuthenticated, SubscriptionActivePermission]

    @extend_schema(
        tags=["PDF Management V3"],
        summary="Initiate booking list PDF export (mobile or desktop list)",
        description=(
            "Starts an asynchronous PDF export of the shop's booking list.\n\n"
            "Purpose: Gives the shop a printable/shareable PDF of exactly the bookings shown in the app's "
            "booking list for the selected filters - Serial No., Pickup Date, Invoice ID, Product names "
            "(as a list), Client name and primary phone, Paid amount and Pending amount - with the shop's "
            "details at the top and a summary (total bookings, total paid, total pending).\n\n"
            "Context: Called from the Export PDF button of the booking list. Send the same filters the list "
            "screen is using and set `platform` to the screen the user is on: `mobile` applies the Mobile "
            "booking list rules (GET /api/v4/bookings/bookings/), `desktop` applies the Desktop booking "
            "list rules (GET /api/v5/bookings/bookings/desktop-list/) - same filters, same validation "
            "messages and the same order. Date-relative statuses use the date of this request. The PDF is "
            "generated in the background: poll GET /api/v3/pdf/status/{task_id}/ until `status` is "
            "`completed`, then download `download_url` (valid 24 hours). An identical request made while a "
            "previous one is still pending/processing (15-minute window) returns that task instead of "
            "starting a new one. At most BOOKING_LIST_PDF_MAX_ROWS (default 5,000) bookings per PDF.\n\n"
            "Authentication: JWT Bearer token; X-Active-Shop-ID header (integer) selects the shop; an "
            "active subscription is required. Rate limit: 20 PDF exports per hour per user.\n\n"
            "Accepted inputs:\n"
            "Request body (JSON) - Required:\n"
            "- platform (string): mobile | desktop.\n\n"
            "Request body (JSON) - Optional (same meaning as the list's query parameters):\n"
            "- status (string), search (string), start_date (DD-MM-YYYY or YYYY-MM-DD), "
            "end_date (DD-MM-YYYY or YYYY-MM-DD), purchase_mode (normal | package | courier | pickup)."
        ),
        parameters=[
            OpenApiParameter(
                name="X-Active-Shop-ID",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.HEADER,
                required=True,
                description=(
                    "Purpose: Selects the shop whose bookings are exported (the user must be an active "
                    "member). Location: request header. Format/Type: integer. Required."
                ),
            ),
        ],
        request=REQUEST_SERIALIZER,
        responses={
            200: OpenApiResponse(description=(
                f"Success. Exact messages: {MSG_STARTED} | {MSG_REUSED}\n\n"
                "Response data (new task): task_id (string, UUID), status ('pending'), platform "
                "('mobile' | 'desktop'), estimated_bookings (integer), estimated_time (string, e.g. '2s').\n"
                "Response data (reused task): task_id, status ('pending' | 'processing'), platform, "
                "elapsed_seconds (integer), reused (true)."
            )),
            400: OpenApiResponse(description=(
                "Validation error (data.validation_errors keyed by field). Exact messages:\n"
                f"- {MSG_INVALID_BODY}\n"
                f"- {MSG_PLATFORM}\n"
                f"- {MSG_TOO_MANY.format(limit=5000)} (the limit follows BOOKING_LIST_PDF_MAX_ROWS)\n"
                "Mobile filters (same as the Mobile booking list):\n"
                "- Invalid purchase_mode.\n"
                "Desktop filters (same as the Desktop booking list):\n"
                "- Invalid start_date format. Use DD-MM-YYYY or YYYY-MM-DD.\n"
                "- Invalid end_date format. Use DD-MM-YYYY or YYYY-MM-DD.\n"
                "- start_date cannot be after end_date.\n"
                "- Invalid status filter.\n"
                "- Invalid purchase_mode filter. Must be one of: normal, package, courier, pickup.\n"
                "- Search query too long. Maximum 100 characters."
            )),
            403: OpenApiResponse(description=(
                "Permission error. Exact messages include: Invalid Shop ID format. Must be an integer.; "
                "Invalid or deleted Shop ID provided.; This shop is inactive.; You don't have access to this "
                "shop.; and the subscription-expired message from SubscriptionActivePermission."
            )),
            429: OpenApiResponse(description="Rate limit exceeded (DRF throttle): 20 PDF exports per hour per user."),
            500: OpenApiResponse(description=f"Server error. Exact messages: {MSG_QUEUE_FAILED} | {MSG_SERVER_ERROR}"),
        },
        examples=[
            OpenApiExample(
                "Desktop request",
                request_only=True,
                value={"platform": "desktop", "status": "upcoming", "start_date": "01-11-2026",
                       "end_date": "30-11-2026", "purchase_mode": "courier"},
            ),
            OpenApiExample(
                "Mobile request",
                request_only=True,
                value={"platform": "mobile", "status": "measurements", "search": "rahul"},
            ),
            OpenApiExample(
                "Success - new task",
                response_only=True,
                status_codes=["200"],
                value={"status": "success", "message": MSG_STARTED, "dev_message": DEV_POLL,
                       "data": {"task_id": "b2f0c7a4-3e1d-4c55-9a0e-6c1f2d7e8a91", "status": "pending",
                                "platform": "desktop", "estimated_bookings": 148, "estimated_time": "2s"}},
            ),
            OpenApiExample(
                "Success - existing task reused",
                response_only=True,
                status_codes=["200"],
                value={"status": "success", "message": MSG_REUSED, "dev_message": DEV_POLL,
                       "data": {"task_id": "b2f0c7a4-3e1d-4c55-9a0e-6c1f2d7e8a91", "status": "processing",
                                "platform": "desktop", "elapsed_seconds": 6, "reused": True}},
            ),
            OpenApiExample(
                "Too many bookings",
                response_only=True,
                status_codes=["400"],
                value={"status": "validation_error", "message": MSG_TOO_MANY.format(limit=5000),
                       "data": {"validation_errors": {"filters": [MSG_TOO_MANY.format(limit=5000)]}},
                       "dev_message": "8240 bookings match; the maximum per PDF is 5000. Ask the user to add a "
                                      "status or date filter."},
            ),
            OpenApiExample(
                "Invalid platform",
                response_only=True,
                status_codes=["400"],
                value={"status": "validation_error", "message": MSG_PLATFORM,
                       "data": {"validation_errors": {"platform": [MSG_PLATFORM]}},
                       "dev_message": "platform must be one of: mobile, desktop."},
            ),
        ],
    )
    def post(self, request):
        # Shop access errors are PermissionDenied -> DRF 403 (kept out of the generic 500 handler).
        shop_id = get_active_shop_id(request)
        try:
            params, error = self._read_body(request.data)
            if error:
                return error

            today = localdate()
            try:
                queryset = build_queryset(params["platform"], shop_id, params, today)
            except BookingListFilterError as e:
                logger.warning(f"[Booking List PDF] Rejected filter for shop {shop_id}: {e.field} - {e.dev_message}")
                return self.validation_error_response(
                    errors={e.field: [e.message]}, message=e.message, dev_message=e.dev_message,
                )

            count = queryset.order_by().count()
            limit = _max_rows()
            if count > limit:
                message = MSG_TOO_MANY.format(limit=limit)
                return self.validation_error_response(
                    errors={"filters": [message]},
                    message=message,
                    dev_message=(
                        f"{count} bookings match; the maximum per PDF is {limit}. "
                        "Ask the user to add a status or date filter."
                    ),
                )

            request_params = {**params, "today": today.isoformat()}
            request_hash = compute_request_hash(request_params)

            pdf_task, reused = self._get_or_create_task(shop_id, request, request_params, request_hash, count)
            if reused:
                logger.info(f"[Booking List PDF] Reusing task {pdf_task.task_id} for shop {shop_id}")
                return self.success_response(
                    data={
                        "task_id": pdf_task.task_id,
                        "status": pdf_task.status,
                        "platform": params["platform"],
                        "elapsed_seconds": int((timezone.now() - pdf_task.created_at).total_seconds()),
                        "reused": True,
                    },
                    message=MSG_REUSED,
                    dev_message=DEV_POLL,
                )

            # Queued only after the PDFTask row is committed, so the worker always finds it.
            try:
                generate_booking_list_pdf_task.delay(pdf_task.task_id, shop_id, request_params)
            except Exception:
                logger.exception(f"[Booking List PDF] Could not queue task {pdf_task.task_id} for shop {shop_id}")
                pdf_task.mark_failed(MSG_QUEUE_FAILED)
                return self.error_response(
                    message=MSG_QUEUE_FAILED,
                    dev_message="The background worker queue is unavailable; retry the request later.",
                    http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            logger.info(
                f"[Booking List PDF] Queued task {pdf_task.task_id} for shop {shop_id} "
                f"({params['platform']}, {count} bookings)"
            )
            return self.success_response(
                data={
                    "task_id": pdf_task.task_id,
                    "status": pdf_task.status,
                    "platform": params["platform"],
                    "estimated_bookings": count,
                    "estimated_time": pdf_task.estimated_time,
                },
                message=MSG_STARTED,
                dev_message=DEV_POLL,
            )

        except Exception:
            logger.exception(f"[Booking List PDF] Export request failed for shop {shop_id}")
            return self.error_response(
                message=MSG_SERVER_ERROR,
                dev_message="Unexpected server error while creating the PDF task; retry the request.",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ---- helpers -------------------------------------------------------------
    def _read_body(self, data):
        """
        (params, error_response). params = {platform, <filter keys>} with plain
        string values (None when not sent) - exactly what the list APIs read
        from their query string.
        """
        if not hasattr(data, "get"):
            return None, self.validation_error_response(
                errors={"request": [MSG_INVALID_BODY]},
                message=MSG_INVALID_BODY,
                dev_message="Request body must be a JSON object.",
            )

        platform = data.get("platform")
        platform = platform.strip().lower() if isinstance(platform, str) else ""
        if platform not in PLATFORMS:
            return None, self.validation_error_response(
                errors={"platform": [MSG_PLATFORM]},
                message=MSG_PLATFORM,
                dev_message=f"platform must be one of: {', '.join(PLATFORMS)}.",
            )

        params = {"platform": platform}
        for key in FILTER_KEYS:
            value = data.get(key)
            if value is None:
                params[key] = None
            elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
                params[key] = str(value)
            else:
                return None, self.validation_error_response(
                    errors={key: [MSG_INVALID_BODY]},
                    message=MSG_INVALID_BODY,
                    dev_message=f"{key} must be a string.",
                )
        return params, None

    @staticmethod
    def _get_or_create_task(shop_id, request, request_params, request_hash, count):
        """
        Reuse an identical pending/processing export from the last 15 minutes,
        else create a new PDFTask. The shop row lock serializes concurrent
        identical requests, so two quick taps never create two tasks.
        """
        with transaction.atomic():
            Shop.objects.select_for_update().only("id").get(id=shop_id)
            existing = (
                PDFTask.objects.filter(
                    shop_id=shop_id,
                    pdf_type=PDF_TYPE,
                    request_hash=request_hash,
                    status__in=("pending", "processing"),
                    created_at__gte=timezone.now() - REUSE_WINDOW,
                )
                .order_by("-created_at")
                .first()
            )
            if existing:
                return existing, True

            pdf_task = PDFTask.objects.create(
                task_id=str(uuid.uuid4()),
                pdf_type=PDF_TYPE,
                shop_id=shop_id,
                user=request.user,
                status="pending",
                request_params=request_params,
                request_hash=request_hash,
                estimated_count=count,
                estimated_time=f"{max(1, count // 80)}s",
            )
            return pdf_task, False
