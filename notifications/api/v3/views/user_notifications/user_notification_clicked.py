from rest_framework.decorators import action
from notifications.models import UserNotification
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema


class UserNotificationClickedMixin:
    """PATCH /notifications/{pk}/clicked/ — Record click + auto-read."""

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Mark notification as clicked",
        description="Records a click timestamp for a single notification and also marks it as read.",
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
            200: OpenApiResponse(description="Exact message: Notification click recorded."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Please provide a valid notification ID.\n"
                    "- Invalid notification ID."
                )
            ),
            404: OpenApiResponse(description="Exact message: Notification not found."),
            500: OpenApiResponse(description="Exact message: Unable to update notification click right now. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="PATCH /api/v3/notifications/12/clicked/",
                value={"id": 12},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Notification click recorded.",
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
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to update notification click right now. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @action(detail=True, methods=["patch"], url_path="clicked")
    def mark_clicked(self, request, pk=None):
        """Record click timestamp (also marks as read if unread)."""
        pk, error_response = self._parse_notification_id(pk)
        if error_response:
            return error_response

        now = timezone.now()
        try:
            updated = UserNotification.objects.filter(
                id=pk, user=request.user, is_deleted=False
            ).update(is_read=True, read_at=now, clicked_at=now)
        except Exception:
            return self.error_response(
                message="Unable to update notification click right now. Please try again.",
                dev_message="mark_clicked endpoint failed during update query.",
            )

        if updated == 0:
            return self.not_found_response(
                message="Notification not found.",
                dev_message=(
                    f"No active UserNotification found for id={pk} "
                    f"and user_id={request.user.id}."
                ),
            )

        return self.success_response(message="Notification click recorded.")
