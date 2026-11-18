from rest_framework.decorators import action
from django.db import transaction, models
from decimal import Decimal
from tailored.models import TailorOrder, CustomerPayment, TailorOrderRefund
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Delete tailor order payment",
    description="Deletes a customer payment from a rental or sale tailor order and recalculates the payment summary.",
    parameters=[OpenApiParameter("payment_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="CustomerPayment id.")],
    responses={
        200: OpenApiResponse(description="Exact success message: Payment deleted successfully."),
        400: OpenApiResponse(description="Exact error messages:\n- Payment cannot be deleted from completed orders.\n- Payment cannot be deleted from processed orders.\n- Payment deletion is only allowed for rental or sale orders.\n- Cannot delete payment - amount exceeds total paid."),
        404: OpenApiResponse(description="Exact error message: Payment record not found."),
        500: OpenApiResponse(description="Exact error messages:\n- Failed to delete payment record.\n- Something went wrong while deleting the payment."),
    },
    examples=[OpenApiExample("Delete payment success", value={"status": "success", "message": "Payment deleted successfully.", "data": {"order_id": 8, "shop_tailor_id": "TO-0008", "deleted_payment_amount": 500, "updated_paid_amount": 0, "remaining_balance": 1500}}, response_only=True, status_codes=["200"])],
)
@action(detail=False, methods=["delete"], url_path="delete-payment/(?P<payment_id>[^/.]+)")
@transaction.atomic
def delete_payment(self, request, *args, **kwargs):
    """
    Delete a customer payment record from a tailor order and update payment status.
    Validates payment belongs to shop and handles all edge cases.
    """
    payment_id = kwargs.get("payment_id")

    try:
        # -------- Step 1: Fetch Payment Without Locking (for validation) --------
        try:
            # First, get payment without select_for_update to avoid JOIN issues
            payment = CustomerPayment.objects.select_related("order").get(
                id=payment_id,
                order__shop_id=self.shop_id,
                order__deleted__isnull=True
            )
        except CustomerPayment.DoesNotExist:
            return self.not_found_response(
                resource="Payment",
                message="Payment record not found.",
                dev_message=f"Payment with ID {payment_id} not found for shop {self.shop_id} or payment belongs to deleted order."
            )

        order_id = payment.order_id
        payment_amount = payment.payment_amount

        # -------- Step 2: Lock Payment and Order Separately --------
        try:
            # Lock the payment record
            locked_payment = CustomerPayment.objects.select_for_update().get(id=payment_id)

            # Lock the tailor order record
            locked_order = TailorOrder.objects.select_for_update().get(id=order_id)

        except (CustomerPayment.DoesNotExist, TailorOrder.DoesNotExist):
            return self.not_found_response(
                resource="Payment",
                message="Payment record not found.",
                dev_message=f"Payment {payment_id} or order {order_id} not found during locking."
            )

        # -------- Business Logic Validations --------

        # 1. Cannot delete payment from completed order
        if locked_order.status == "completed":
            return self.validation_error_response(
                errors={"order_status": ["Cannot delete payment from completed order."]},
                message="Payment cannot be deleted from completed orders.",
                dev_message=f"Order {locked_order.id} is completed, payment deletion not allowed."
            )

        # 2. Cannot delete payment if order is already processed to booking/sale
        if locked_order.processed_to_booking_sale:
            return self.validation_error_response(
                errors={"processed_status": ["Cannot delete payment from orders already processed to booking or sale."]},
                message="Payment cannot be deleted from processed orders.",
                dev_message=f"Order {locked_order.id} is already processed to booking/sale, payment deletion not allowed."
            )

        # 3. Only allow deletion for rental/sale orders (shop orders don't have customer payments)
        if locked_order.customer_order_type not in ["rental", "sale"]:
            return self.validation_error_response(
                errors={"order_type": ["Can only delete payments from rental or sale orders."]},
                message="Payment deletion is only allowed for rental or sale orders.",
                dev_message=f"Order {locked_order.id} is of type '{locked_order.customer_order_type}', not rental/sale."
            )

        # 4. Reconcile remaining payments against refunds to preserve net paid amount
        payment_stats = CustomerPayment.objects.filter(
            order_id=order_id
        ).aggregate(
            total_count=models.Count('id'),
            other_payments_total=models.Sum('payment_amount', filter=~models.Q(id=payment_id))
        )

        refund_stats = TailorOrderRefund.objects.filter(
            order_id=order_id
        ).aggregate(
            total_refunds=models.Sum('refunded_amount')
        )

        total_payments_count = payment_stats['total_count'] or 0
        other_payments_total = payment_stats['other_payments_total'] or 0
        total_refunds = Decimal(str(refund_stats['total_refunds'] or 0))

        # -------- Calculate New Payment State --------
        current_paid = locked_order.paid_amount or 0
        new_paid_amount = current_paid - payment_amount
        recalculated_new_paid_amount = other_payments_total - total_refunds

        # Ensure new paid amount doesn't go negative
        if new_paid_amount < 0:
            return self.validation_error_response(
                errors={"payment_amount": ["Payment amount exceeds total paid amount."]},
                message="Cannot delete payment - amount exceeds total paid.",
                dev_message=f"Deleting payment {payment_id} would result in negative paid amount. Current: {current_paid}, Payment: {payment_amount}."
            )

        if recalculated_new_paid_amount < 0:
            return self.validation_error_response(
                errors={"payment_amount": ["Delete related refund entries before deleting this payment."]},
                message="Cannot delete payment - amount exceeds total paid.",
                dev_message=(
                    f"Deleting payment {payment_id} would make net paid negative. "
                    f"remaining_payments={other_payments_total}, total_refunds={total_refunds}, "
                    f"recalculated_new_paid_amount={recalculated_new_paid_amount}"
                )
            )

        # Verify our calculation matches the persisted payment/refund history.
        if recalculated_new_paid_amount != new_paid_amount:
            logger.warning(
                "Payment calculation mismatch for order %s. Expected by current paid amount: %s, "
                "recalculated from history: %s (remaining_payments=%s, refunds=%s)",
                locked_order.id,
                new_paid_amount,
                recalculated_new_paid_amount,
                other_payments_total,
                total_refunds,
            )
            new_paid_amount = recalculated_new_paid_amount

        # -------- Calculate Remaining Balance --------
        customer_total_charge = locked_order.customer_total_charge or 0
        discount_amount = locked_order.discount_amount or 0
        final_payable = customer_total_charge - discount_amount

        remaining_balance = max(final_payable - new_paid_amount, 0) if final_payable > 0 else 0

        # -------- Create Savepoint for Nested Transaction --------
        savepoint = transaction.savepoint()

        try:
            # -------- Delete Ledger Entry (new flow only) --------
            if locked_payment.account_id:
                from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
                _delete_ledger_entries(
                    reference_ids=[locked_payment.id],
                    reference_type='CustomerPayment',
                )

            # -------- Delete Payment Record --------
            locked_payment.delete()
            logger.info(f"Customer payment {payment_id} deleted successfully for order {locked_order.id}")

            # -------- Update Order Payment Information --------
            locked_order.paid_amount = new_paid_amount
            locked_order.save(update_fields=['paid_amount'])

            # -------- Commit Transaction --------
            transaction.savepoint_commit(savepoint)

            try:
                create_activity_log(
                    shop=locked_order.shop,
                    user=request.user,
                    instance=locked_order,
                    action="Payment_deleted",
                    message=(
                        f"Deleted payment of ₹{payment_amount}."
                    )
                )
            except Exception as log_error:
                logger.warning(f"Failed to create activity log for payment deletion: {log_error}")

            # -------- Build Success Response --------
            return self.success_response(
                data={
                    "order_id": locked_order.id,
                    "shop_tailor_id": locked_order.shop_tailor_id,
                    "deleted_payment_amount": payment_amount,
                    "updated_paid_amount": new_paid_amount,
                    "remaining_balance": remaining_balance,
                    "total_payments_remaining": total_payments_count - 1,
                    "customer_total_charge": customer_total_charge,
                    "discount_amount": discount_amount,
                    "final_payable_amount": final_payable
                },
                message="Payment deleted successfully."
            )

        except Exception as e:
            # -------- Rollback on Failure --------
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Failed to delete customer payment {payment_id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to delete payment record.",
                dev_message=f"Database error while deleting payment {payment_id}: {str(e)}"
            )

    except Exception as e:
        logger.error(f"Customer payment deletion error for payment {payment_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while deleting the payment.",
            dev_message=f"Unexpected error in delete_payment for payment {payment_id}: {str(e)}"
        )
