import logging

from django.db import transaction
from django.utils import timezone
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from purchase.models import Purchase
from ERP.utils.shop_activity_logs import create_activity_log

logger = logging.getLogger("api_requests")


def _format_datetime(value):
    """Render a datetime as 'YYYY-MM-DD HH:MM:SS' in local time, or None."""
    if not value:
        return None
    return localtime(value).strftime("%Y-%m-%d %H:%M:%S")


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Mark purchase as completed",
    description=(
        "Marks a purchase as completed once the vendor has been fully paid.\n\n"
        "Purpose: Closes out a purchase for audit/reporting once its bill is "
        "settled - a completed purchase is locked against payment deletion "
        "(see delete-payment) and its is_completed/completed_at become the "
        "source of truth for 'this purchase order is finished'.\n\n"
        "Context: Called from the purchase detail screen once paid_amount "
        "reaches purchase_total. Applicable to both with_products and "
        "without_products purchases - paid_amount vs purchase_total is the "
        "same basis for both (Purchase.is_fully_paid/outstanding_balance); "
        "additional_charges are excluded, since they're separately-settled "
        "on-site expenses, not part of the vendor's payable balance. "
        "is_completed cannot be set via the update (PATCH) endpoint - this "
        "is the only way to complete a purchase, so the fully-paid check "
        "below can never be bypassed.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- pk: Purchase ID."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purchase ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Purchase marked as completed successfully."),
        400: OpenApiResponse(
            description=(
                "Exact messages include:\n"
                "- This purchase is already marked as completed.\n"
                "- Payment not completed. Outstanding balance: ₹<outstanding_balance>."
            )
        ),
        404: OpenApiResponse(description="Exact message: Purchase not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while completing the purchase."),
    },
)
@action(detail=False, methods=["post"], url_path="mark-completed/(?P<pk>[^/.]+)")
@transaction.atomic
def mark_completed(self, request, *args, **kwargs):
    pk = kwargs.get("pk")
    try:
        # Lock the row so a concurrent add-payment/delete-payment/another
        # completion request against the same purchase can't race past a
        # stale paid_amount/is_completed read.
        purchase = Purchase.objects.select_for_update().filter(
            id=pk, shop_id=self.shop_id, deleted__isnull=True
        ).first()
        if not purchase:
            return self.not_found_response(resource="Purchase", message="Purchase not found.")

        if purchase.is_completed:
            return self.validation_error_response(
                errors={"is_completed": ["This purchase is already marked as completed."]},
                message="This purchase is already marked as completed.",
            )

        if not purchase.is_fully_paid:
            outstanding = purchase.outstanding_balance
            message = (
                f"Payment not completed. "
                f"Outstanding balance: ₹{outstanding}."
            )
            return self.validation_error_response(
                errors={"paid_amount": [message]},
                message=message,
            )

        purchase.is_completed = True
        purchase.completed_at = timezone.now()
        purchase.save(update_fields=["is_completed", "completed_at"])

        try:
            create_activity_log(
                shop=purchase.shop,
                user=request.user,
                instance=purchase,
                action="completed",
                message=f"Purchase {purchase.purchase_code} marked as completed.",
            )
        except Exception:
            logger.exception("Failed to create activity log for purchase completion")

        return self.success_response(
            data={
                "purchase_id": purchase.id,
                "is_completed": purchase.is_completed,
                "completed_at": _format_datetime(purchase.completed_at),
                "paid_amount": purchase.paid_amount,
                "purchase_total": purchase.purchase_total,
            },
            message="Purchase marked as completed successfully.",
        )

    except Exception as e:
        logger.error(f"Purchase mark_completed error for id {pk}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while completing the purchase.",
            dev_message=str(e),
        )


__all__ = ["mark_completed"]
