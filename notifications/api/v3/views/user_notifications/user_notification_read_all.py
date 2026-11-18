from rest_framework.decorators import action
from notifications.models import UserNotification
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema


class UserNotificationReadAllMixin:
    """PATCH /notifications/read-all/ — Mark all unread as read."""

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Mark all notifications as read",
        description="Marks all unread notifications as read for the authenticated user within the active shop context.",
        responses={
            200: OpenApiResponse(description="Success message pattern: {count} notification(s) marked as read."),
            500: OpenApiResponse(description="Exact message: Unable to mark all notifications as read right now. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="PATCH /api/v3/notifications/read-all/",
                value={},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "3 notifications marked as read.",
                    "data": {"updated_count": 3},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to mark all notifications as read right now. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @action(detail=False, methods=["patch"], url_path="read-all")
    def mark_all_read(self, request):
        """Mark all unread notifications as read for the current user & shop."""
        try:
            shop_id = self._get_shop_id(request)
            now = timezone.now()

            qs = UserNotification.objects.filter(
                user=request.user, is_deleted=False, is_read=False
            )
            if shop_id:
                qs = qs.filter(shop_id=shop_id)

            updated = qs.update(is_read=True, read_at=now)

            return self.success_response(
                data={"updated_count": updated},
                message=f"{updated} notification{'s' if updated != 1 else ''} marked as read.",
            )
        except Exception:
            return self.error_response(
                message="Unable to mark all notifications as read right now. Please try again.",
                dev_message="mark_all_read endpoint failed during bulk update.",
            )
