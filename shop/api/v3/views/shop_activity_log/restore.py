from rest_framework.decorators import action
from django.db import transaction
from shop.models import ShopActivityLog
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from .helpers.restorers import RESTORER_REGISTRY
import logging

logger = logging.getLogger(__name__)

# Keep action names centralized so adding new restorable actions requires one change point.
RESTORABLE_ACTIONS = ("deleted", "variant_deleted")


@extend_schema(
    tags=["Shop Management V3"],
    summary="Restore a deleted entity from activity log",
    description=(
        "Restores a soft-deleted entity identified by a ShopActivityLog ID "
        "(action must be 'deleted' for bookings/products, or 'variant_deleted' for product variants). "
        "Currently supports: BookingV2, Product, ProductVariant. "
        "For bookings, runs the same availability conflict checks as booking creation before restoring. "
        "For products, validates service status, plan limit, name uniqueness, and variant QR codes; "
        "cascade-restores all soft-deleted variants. "
        "For product variants, validates parent product is active, QR code, and attribute uniqueness. "
        "Creates a linked restore log entry (restored_from_activity) so the UI can detect already-restored state."
    ),
    parameters=[
        OpenApiParameter(
            "pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True,
            description="ShopActivityLog ID of the delete action to restore."
        ),
    ],
    responses={
        200: OpenApiResponse(description="Entity restored successfully."),
        400: OpenApiResponse(
            description=(
                "Conflict detected (same shape as booking creation conflict error), "
                "or booking has already been restored."
            )
        ),
        404: OpenApiResponse(description="Activity log not found, not a delete action, or deleted entity not found."),
        422: OpenApiResponse(description="Entity type not yet supported for restore."),
        500: OpenApiResponse(description="Unable to process restore request. Please try again later."),
    },
    examples=[],
)
@action(detail=False, methods=["post"], url_path=r"restore/(?P<pk>[^/.]+)")
def restore(self, request, pk=None):
    try:
        with transaction.atomic():
            # Lock the target delete log row so concurrent restore requests serialize.
            log = (
                ShopActivityLog.objects
                .select_for_update()
                .filter(id=pk, shop_id=self.shop_id, action__in=RESTORABLE_ACTIONS)
                .first()
            )
            if not log:
                return self.not_found_response(
                    resource="ActivityLog",
                    message="Activity log not found."
                )

            if not log.content_type or not log.object_id:
                return self.validation_error_response(
                    errors={"log": ["This log entry has no associated entity."]},
                    message="Cannot restore: no entity linked to this log."
                )

            # Re-check restore state while holding the row lock.
            if ShopActivityLog.objects.filter(restored_from_activity_id=log.id).exists():
                return self.validation_error_response(
                    errors={"log": ["This record has already been restored."]},
                    message="This record has already been restored."
                )

            model_name = log.content_type.model  # e.g. "bookingv2"
            restorer = RESTORER_REGISTRY.get(model_name)

            if not restorer:
                return self.error_response(
                    message=f"Restore is not yet supported for '{log.content_type.name}'.",
                    status_code=422
                )

            return restorer(self, log, request)

    except Exception as e:
        logger.error(f"Restore action failed for log {pk}: {e}", exc_info=True)
        return self.error_response(
            message="Unable to process restore request. Please try again later.",
            dev_message=str(e)
        )
