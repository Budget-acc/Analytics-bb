from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework import viewsets
from notifications.models import UserNotification, Notification
from notifications.api.v3.serializers import UserNotificationSerializer
from ERP.utils.mixins import StandardResponseMixin
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


class BaseUserNotificationViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Base ViewSet for user notification management.

    Contains shared helpers, authentication, list, unread-count, and mark-read.
    Action-specific logic lives in separate mixin files.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]
    pagination_class = StandardPageNumberPagination
    ALLOWED_TYPE_FILTERS = {choice[0] for choice in Notification.TYPE_CHOICES}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_shop_id(self, request):
        """Resolve shop_id safely; returns int or None."""
        try:
            return get_active_shop_id(request)
        except Exception:
            return None

    def _parse_notification_id(self, pk):
        """Validate notification primary key for detail actions."""
        try:
            return int(pk), None
        except (ValueError, TypeError):
            return None, self.validation_error_response(
                {"id": ["Invalid notification ID."]},
                message="Please provide a valid notification ID.",
                dev_message="Expected integer notification id in URL path.",
            )

    def _parse_bool_query(self, raw_value):
        """Parse boolean query param values safely."""
        if raw_value is None:
            return None, None

        normalized = str(raw_value).strip().lower()
        if normalized in ("true", "1", "yes"):
            return True, None
        if normalized in ("false", "0", "no"):
            return False, None

        return None, self.validation_error_response(
            {"is_read": ["Invalid boolean value. Use true or false."]},
            message="Please provide a valid read filter.",
            dev_message="Invalid query param 'is_read'. Allowed values: true/false/1/0/yes/no.",
        )

    def _base_queryset(self, request):
        """
        Base queryset scoped to the authenticated user and their active shop.
        Excludes soft-deleted notifications.
        Uses select_related for Notification FK to avoid N+1.
        """
        shop_id = self._get_shop_id(request)

        qs = (
            UserNotification.objects
            .filter(user=request.user, is_deleted=False)
            .select_related(
                "notification",
                "notification__content_type",
                "notification__shop",
            )
            .order_by("-received_at")
        )

        if shop_id:
            qs = qs.filter(shop_id=shop_id)

        return qs

    # ------------------------------------------------------------------
    # GET /notifications/
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notification Management V3"],
        summary="List user notifications",
        description=(
            "Returns paginated notifications for the authenticated user, optionally filtered by notification type and read status."
        ),
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Optional notification type filter. Allowed values: booking, sale, expiry, stock, tailor, system.",
            ),
            OpenApiParameter(
                name="is_read",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Optional read-status filter. Allowed values: true, false, 1, 0, yes, no.",
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Pagination page number.",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Pagination page size.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Paginated notification list response."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Please provide a valid notification type.\n"
                    "- Invalid notification type.\n"
                    "- Please provide a valid read filter.\n"
                    "- Invalid boolean value. Use true or false."
                )
            ),
            500: OpenApiResponse(description="Exact message: Unable to fetch notifications right now. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "type": "booking",
                    "is_read": "false",
                    "page": 1,
                    "page_size": 15,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "count": 2,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 12,
                            "notification": {
                                "id": 90,
                                "title": "Booking payment received",
                                "body": "An advance payment was recorded.",
                                "data": {"booking_id": 223},
                                "image_url": None,
                                "notification_type": "booking",
                                "priority": "normal",
                                "content_type": 14,
                                "object_id": 223,
                                "created_at": "04-05-2026 10:30:00"
                            },
                            "is_read": False,
                            "push_status": "sent",
                            "received_at": "04-05-2026 10:31:00",
                            "read_at": None,
                            "clicked_at": None,
                        }
                    ],
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid type response",
                value={
                    "status": "validation_error",
                    "message": "Please provide a valid notification type.",
                    "data": {
                        "validation_errors": {
                            "type": ["Invalid notification type."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Invalid read filter response",
                value={
                    "status": "validation_error",
                    "message": "Please provide a valid read filter.",
                    "data": {
                        "validation_errors": {
                            "is_read": ["Invalid boolean value. Use true or false."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to fetch notifications right now. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def list(self, request):
        """
        Paginated notification list with optional filters.

        Query params:
            type        – filter by notification_type (booking, sale, expiry, etc.)
            is_read     – "true" / "false"
            page        – page number (default 1)
            page_size   – items per page (default 15, max 100)
        """
        try:
            qs = self._base_queryset(request)

            # --- Optional filters ---
            notif_type = request.query_params.get("type")
            if notif_type:
                notif_type = str(notif_type).strip().lower()
                if notif_type not in self.ALLOWED_TYPE_FILTERS:
                    return self.validation_error_response(
                        {"type": ["Invalid notification type."]},
                        message="Please provide a valid notification type.",
                        dev_message=(
                            "Invalid query param 'type'. Allowed values: "
                            f"{sorted(self.ALLOWED_TYPE_FILTERS)}"
                        ),
                    )
                qs = qs.filter(notification__notification_type=notif_type)

            is_read_val, is_read_error = self._parse_bool_query(
                request.query_params.get("is_read")
            )
            if is_read_error:
                return is_read_error
            if is_read_val is not None:
                qs = qs.filter(is_read=is_read_val)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(qs, request)
            serializer = UserNotificationSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        except Exception:
            logger.exception(
                "Failed to list notifications for user=%s",
                getattr(request.user, "id", None),
            )
            return self.error_response(
                message="Unable to fetch notifications right now. Please try again.",
                dev_message="Notification list endpoint failed while building paginated response.",
            )

    # ------------------------------------------------------------------
    # GET /notifications/unread-count/
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Get unread notification count",
        description="Returns the total unread notification count for the authenticated user within the active shop context.",
        responses={
            200: OpenApiResponse(description="Exact message: Unread count retrieved successfully."),
            500: OpenApiResponse(description="Exact message: Unable to fetch unread count right now. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /api/v3/notifications/unread-count/",
                value={},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Unread count retrieved successfully.",
                    "data": {"unread_count": 4},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to fetch unread count right now. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """Return the count of unread notifications for the current user & shop."""
        try:
            shop_id = self._get_shop_id(request)

            qs = UserNotification.objects.filter(
                user=request.user, is_deleted=False, is_read=False
            )
            if shop_id:
                qs = qs.filter(shop_id=shop_id)

            count = qs.count()

            return self.success_response(
                data={"unread_count": count},
                message="Unread count retrieved successfully.",
            )
        except Exception:
            logger.exception(
                "Failed unread count for user=%s",
                getattr(request.user, "id", None),
            )
            return self.error_response(
                message="Unable to fetch unread count right now. Please try again.",
                dev_message="Unread count endpoint failed during aggregate count query.",
            )

    # ------------------------------------------------------------------
    # PATCH /notifications/{pk}/read/
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Mark notification as read",
        description="Marks a single notification as read. If the notification is already read, the API returns a success message without modifying the record.",
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="User notification id.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Exact messages: Notification marked as read. or Notification is already read."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Please provide a valid notification ID.\n"
                    "- Invalid notification ID."
                )
            ),
            404: OpenApiResponse(description="Exact message: Notification not found."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="PATCH /api/v3/notifications/12/read/",
                value={"id": 12},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Notification marked as read.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Already read response",
                value={
                    "status": "success",
                    "message": "Notification is already read.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid ID response",
                value={
                    "status": "validation_error",
                    "message": "Please provide a valid notification ID.",
                    "data": {
                        "validation_errors": {
                            "id": ["Invalid notification ID."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Not found response",
                value={
                    "status": "not_found",
                    "message": "Notification not found.",
                    "data": None,
                },
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    @action(detail=True, methods=["patch"], url_path="read")
    def mark_read(self, request, pk=None):
        """Mark a single notification as read (race-condition safe)."""
        pk, error_response = self._parse_notification_id(pk)
        if error_response:
            return error_response

        now = timezone.now()
        updated = UserNotification.objects.filter(
            id=pk, user=request.user, is_deleted=False, is_read=False
        ).update(is_read=True, read_at=now)

        if updated == 0:
            exists = UserNotification.objects.filter(
                id=pk, user=request.user, is_deleted=False
            ).exists()
            if not exists:
                return self.not_found_response(
                    message="Notification not found.",
                    dev_message=(
                        f"No active UserNotification found for id={pk} "
                        f"and user_id={request.user.id}."
                    ),
                )
            return self.success_response(message="Notification is already read.")

        return self.success_response(message="Notification marked as read.")
