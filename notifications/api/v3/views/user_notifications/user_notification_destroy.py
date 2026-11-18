from notifications.models import UserNotification
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema


class UserNotificationDestroyMixin:
    """DELETE /notifications/{pk}/ — Soft-delete a single notification."""

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Delete notification",
        description="Soft-deletes a single notification for the authenticated user.",
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
            200: OpenApiResponse(description="Exact message: Notification deleted successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Please provide a valid notification ID.\n"
                    "- Invalid notification ID."
                )
            ),
            404: OpenApiResponse(description="Exact message: Notification not found."),
            500: OpenApiResponse(description="Exact message: Unable to delete notification right now. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="DELETE /api/v3/notifications/12/",
                value={"id": 12},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Notification deleted successfully.",
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
                    "message": "Unable to delete notification right now. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def destroy(self, request, pk=None):
        """Soft-delete a single notification (keeps DB history)."""
        pk, error_response = self._parse_notification_id(pk)
        if error_response:
            return error_response

        try:
            updated = UserNotification.objects.filter(
                id=pk, user=request.user, is_deleted=False
            ).update(is_deleted=True)
        except Exception:
            return self.error_response(
                message="Unable to delete notification right now. Please try again.",
                dev_message="destroy endpoint failed during soft-delete update.",
            )

        if updated == 0:
            return self.not_found_response(
                message="Notification not found.",
                dev_message=(
                    f"No active UserNotification found for id={pk} "
                    f"and user_id={request.user.id}."
                ),
            )

        return self.success_response(message="Notification deleted successfully.")
