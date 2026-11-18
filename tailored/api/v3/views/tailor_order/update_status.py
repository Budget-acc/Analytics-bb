from decimal import Decimal
from rest_framework.decorators import action
from tailored.models import TailorOrder
from django.utils import timezone
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


def _to_decimal(value):
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


_MODEL_STATUS_VALUES = {value for value, _ in TailorOrder.STATUS_CHOICES}

# Business rules per order type. Values are intersected with model statuses,
# so this mapping remains resilient when STATUS_CHOICES evolves.
_STATUS_POLICY_BY_ORDER_TYPE = {
    "shop": ["initiated", "in_progress", "completed", "cancelled"],
    "sale": ["initiated", "in_progress", "ready_to_deliver", "delivered", "completed", "cancelled"],
    "rental": ["initiated", "in_progress", "ready_to_deliver", "delivered", "returned", "completed", "cancelled"],
}

ALLOWED_STATUSES_BY_ORDER_TYPE = {
    order_type: [status for status in statuses if status in _MODEL_STATUS_VALUES]
    for order_type, statuses in _STATUS_POLICY_BY_ORDER_TYPE.items()
}


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Update tailor order status",
    description=(
        "Updates the status of a tailor order in the active shop. "
        "Allowed statuses depend on the order's customer_order_type:\n\n"
        "- **shop**: initiated, in_progress, completed\n"
        "- **sale**: initiated, in_progress, ready_to_deliver, delivered, completed, cancelled\n"
        "- **rental**: initiated, in_progress, ready_to_deliver, delivered, returned, completed, cancelled"
    ),
    parameters=[OpenApiParameter("order_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor order id.")],
    request=inline_serializer(name="CustomWorkV3UpdateTailorOrderStatusRequest", fields={"status": serializers.CharField(required=True, help_text="Status value allowed for the order's customer_order_type.")}),
    responses={
        200: OpenApiResponse(description="Success response with completed_at when applicable."),
        400: OpenApiResponse(description="Exact error messages:\n- Invalid order ID provided.\n- Please select a status.\n- Cannot modify a completed order.\n- Invalid status for {order_type} order.\n- Pending payment must be cleared before completing the order."),
        404: OpenApiResponse(description="Exact error message: Order not found."),
        500: OpenApiResponse(description="Exact error message: Unable to update order status. Please try again."),
    },
    examples=[OpenApiExample("Status success", value={"status": "success", "message": "Order status updated from 'initiated' to 'completed' successfully.", "data": {"order_id": 8, "completed_at": "2026-05-04T10:00:00+05:30"}}, response_only=True, status_codes=["200"])],
)
@action(detail=False, methods=['patch'], url_path=r'update-status/(?P<order_id>\d+)')
def update_status(self, request, order_id=None):
    # Validate order_id parameter
    try:
        order_id = int(order_id)
    except (ValueError, TypeError):
        return self.validation_error_response(
            message="Invalid order ID provided.",
            dev_message=f"order_id is not a valid integer: {order_id}"
        )

    # Validate status field is present
    status = request.data.get("status")
    if not status:
        return self.validation_error_response(
            errors={"status": ["Status is required."]},
            message="Please select a status.",
            dev_message="Missing status field"
        )

    try:
        # Fetch the order first — order type is needed to validate status
        try:
            order = TailorOrder.objects.get(
                id=order_id,
                shop_id=self.shop_id,
                deleted__isnull=True
            )
        except TailorOrder.DoesNotExist:
            return self.not_found_response(
                resource="Tailor Order",
                message="Order not found.",
                dev_message=f"Order {order_id} not found in shop {self.shop_id} or has been deleted"
            )

        if order.status == "completed":
            return self.validation_error_response(
                errors={"status": ["Cannot modify a completed order."]},
                message="Cannot modify a completed order.",
                dev_message=f"TailorOrder {order_id} status=completed.",
            )

        # Validate status against the allowlist for this order's type
        allowed_statuses = ALLOWED_STATUSES_BY_ORDER_TYPE.get(order.customer_order_type, [])
        if status not in allowed_statuses:
            return self.validation_error_response(
                errors={"status": [f"Status must be one of: {', '.join(allowed_statuses)}"]},
                message=f"Invalid status for {order.customer_order_type} order.",
                dev_message=f"Status '{status}' not allowed for order type '{order.customer_order_type}'"
            )

        # Balance check: only for "completed" and only when not converted to a booking
        if status == 'completed' and not order.created_booking_id and order.customer_order_type != 'shop':
            items = list(order.items.all())
            additional_charges = list(order.additional_charges.all())

            total_product_charge = sum((_to_decimal(i.customer_item_charge) for i in items), Decimal("0"))
            total_additional_charges = sum((_to_decimal(c.amount) for c in additional_charges), Decimal("0"))
            security_amount = _to_decimal(order.security_amount)
            paid_amount = _to_decimal(order.paid_amount)
            discount_amount = _to_decimal(order.discount_amount)

            total_amount_with_security = total_product_charge + total_additional_charges + security_amount
            total_paid_with_security = paid_amount + security_amount
            balance = total_amount_with_security - total_paid_with_security - discount_amount

            if balance > 0:
                return self.validation_error_response(
                    errors={"status": ["Pending payment must be cleared before completing the order."]},
                    message="Pending payment must be cleared before completing the order.",
                    dev_message=f"Balance {balance} > 0 for order {order_id}"
                )

        # Update the status
        old_status = order.status
        order.status = status

        if status == 'completed' and old_status != 'completed':
            order.completed_at = timezone.now()

        order.save()

        try:
            create_activity_log(
                shop=order.shop,
                user=request.user,
                instance=order,
                action="updated",
                message=f"Order status changed."
            )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for tailor order update: {log_error}")

        response_data = {
            "order_id": order.id,
            "completed_at": order.completed_at.isoformat() if order.completed_at else None
        }

        return self.success_response(
            data=response_data,
            message=f"Order status updated from '{old_status}' to '{order.status}' successfully.",
            dev_message=f"Status updated for order {order.shop_tailor_id}"
        )

    except Exception as e:
        logger.error(f"Error updating status for order {order_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to update order status. Please try again.",
            dev_message=f"Status update failed: {str(e)}"
        )
