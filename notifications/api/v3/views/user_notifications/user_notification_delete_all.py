from rest_framework.decorators import action
from notifications.models import UserNotification
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema


class UserNotificationDeleteAllMixin:
    """DELETE /notifications/delete-all/ — Soft-delete all notifications."""

    @extend_schema(
        tags=["Notification Management V3"],
        summary="Delete all notifications",
        description="Soft-deletes all notifications for the authenticated user within the active shop context.",
        responses={
            200: OpenApiResponse(description="Success message pattern: {count} notification(s) deleted."),
            500: OpenApiResponse(description="Exact message: Unable to delete notifications right now. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="DELETE /api/v3/notifications/delete-all/",
                value={},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "5 notifications deleted.",
                    "data": {"deleted_count": 5},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to delete notifications right now. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @action(detail=False, methods=["delete"], url_path="delete-all")
    def delete_all(self, request):
        """Soft-delete all notifications for the current user & shop."""
        try:
            shop_id = self._get_shop_id(request)

            qs = UserNotification.objects.filter(
                user=request.user, is_deleted=False
            )
            if shop_id:
                qs = qs.filter(shop_id=shop_id)

            deleted = qs.update(is_deleted=True)

            return self.success_response(
                data={"deleted_count": deleted},
                message=f"{deleted} notification{'s' if deleted != 1 else ''} deleted.",
            )
        except Exception:
            return self.error_response(
                message="Unable to delete notifications right now. Please try again.",
                dev_message="delete_all endpoint failed during bulk soft-delete update.",
            )
