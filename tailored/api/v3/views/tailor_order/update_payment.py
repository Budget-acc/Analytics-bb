from rest_framework.decorators import action
from django.db import transaction
from datetime import datetime as _datetime
from django.utils.timezone import now as _now, make_aware as _make_aware, localtime as _localtime
from tailored.models import TailorOrder, CustomerPayment
from ERP.utils.shop_activity_logs import create_activity_log
from decimal import Decimal, ROUND_HALF_UP
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Add payment to tailor order",
    description="Records an additional customer payment against a rental or sale tailor order.",
    parameters=[OpenApiParameter("order_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor order id.")],
    request=inline_serializer(
        name="CustomWorkV3UpdateTailorOrderPaymentRequest",
        fields={
            "amount": serializers.DecimalField(required=True, max_digits=12, decimal_places=2),
            "payment_method": serializers.CharField(required=False, help_text="Allowed values: cash, upi unless account_id is supplied."),
            "account_id": serializers.IntegerField(required=False, allow_null=True),
            "payment_date": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Optional historical payment date. "
                    "Formats: DD-MM-YYYY (date only) or DD-MM-YYYYTHH:MM:SS (date + time). "
                    "When only a date is provided, the current server time is used as the time component "
                    "so the timestamp is never midnight. "
                    "If omitted, defaults to the current date and time. "
                    "Affects ledger entry date and daily balance calculation; activity log always uses current time."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response with updated paid and remaining balances."),
        400: OpenApiResponse(description="Exact error messages:\n- Invalid order provided.\n- Please enter the payment amount.\n- Payment amount must be greater than zero.\n- Please enter a valid payment amount.\n- Invalid account selected.\n- Please select a valid payment method.\n- Cannot modify a completed order.\n- Payment amount cannot exceed remaining balance of ₹{remaining_balance}.\n- This order is already fully paid.\n- Invalid payment date format."),
        404: OpenApiResponse(description="Exact error message: Order not found."),
        500: OpenApiResponse(description="Exact error message: Unable to process payment. Please try again."),
    },
    examples=[OpenApiExample("Payment success", value={"status": "success", "message": "Payment of ₹500 recorded successfully. Remaining balance: ₹1000", "data": {"total_customer_charge": "1500", "paid_amount": "500", "remaining_balance": "1000"}}, response_only=True, status_codes=["200"])],
)
@action(detail=False, methods=['patch'], url_path=r'update-payment/(?P<order_id>\d+)')
def update_payment(self, request, order_id=None):
    """
    Update customer payment for rental/sale tailor orders.

    URL: /tailor-orders/update-payment/{order_id}/

    Request Body:
    {
        "amount": 25,        // Integer or float - will be converted to integer
        "payment_method": "cash"  // or "upi"
    }

    Only works for orders with customer_order_type = 'rental' or 'sale'
    All calculations are done as integers (₹)
    """

    # Validate order_id parameter
    try:
        order_id = int(order_id)
    except (ValueError, TypeError):
        return self.validation_error_response(
            message="Invalid order provided.",
            dev_message=f"order_id is not a valid integer: {order_id}"
        )

    # Validate request body
    amount = request.data.get("amount")
    payment_method = request.data.get("payment_method", "cash")

    # Validate amount is provided
    if amount is None or amount == "":
        return self.validation_error_response(
            errors={"amount": ["Payment amount is required."]},
            message="Please enter the payment amount.",
            dev_message="Missing or empty amount field"
        )

    # Validate and convert amount to integer (round half-up; handles int/float/Decimal/str)
    try:
        payment_amount = int(Decimal(str(amount)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

        if payment_amount <= 0:
            return self.validation_error_response(
                errors={"amount": ["Payment amount must be greater than zero."]},
                message="Payment amount must be greater than zero.",
                dev_message=f"Non-positive payment amount: {payment_amount}"
            )
    except (ValueError, TypeError, OverflowError):
        return self.validation_error_response(
            errors={"amount": ["Invalid payment amount format."]},
            message="Please enter a valid payment amount.",
            dev_message=f"Amount conversion to integer failed: {amount}"
        )

    # Parse optional historical payment date
    payment_date_str = request.data.get("payment_date")
    payment_date = None
    if payment_date_str:
        # Try date+time format first
        try:
            payment_date = _make_aware(_datetime.strptime(payment_date_str, "%d-%m-%YT%H:%M:%S"))
        except ValueError:
            # Date-only: combine requested date with current time (avoid midnight default)
            try:
                parsed_date = _datetime.strptime(payment_date_str, "%d-%m-%Y").date()
                payment_date = _make_aware(_datetime.combine(parsed_date, _localtime(_now()).time()))
            except ValueError:
                return self.validation_error_response(
                    errors={"payment_date": ["Invalid format. Use DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."]},
                    message="Invalid payment date format."
                )
        # Reject future payment dates
        if payment_date > _now():
            return self.validation_error_response(
                errors={"payment_date": ["payment_date cannot be a future date."]},
                message="Date cannot be a future date.",
            )

    # Validate account_id (new flow) or payment_method (old flow)
    account_id = request.data.get("account_id")
    payment_account = None

    if account_id:
        from finance.models import Account as _Account
        try:
            payment_account = _Account.objects.get(
                id=account_id,
                shop_id=self.shop_id,
                deleted__isnull=True,
                is_active=True,
            )
            payment_method = 'cash' if payment_account.account_type == 'cash' else 'upi'
        except _Account.DoesNotExist:
            return self.validation_error_response(
                errors={'account_id': ['Invalid or inactive account.']},
                message='Invalid account selected.',
            )
    elif payment_method not in ["cash", "upi"]:
        return self.validation_error_response(
            errors={"payment_method": ["Payment method must be 'cash' or 'upi'."]},
            message="Please select a valid payment method.",
            dev_message=f"Invalid payment_method: {payment_method}"
        )

    try:
        # Fetch order with atomic lock
        with transaction.atomic():
            try:
                order = TailorOrder.objects.select_for_update().get(
                    id=order_id,
                    shop_id=self.shop_id,
                    customer_order_type__in=['rental', 'sale'],  # Only rental/sale orders
                    deleted__isnull=True
                )
            except TailorOrder.DoesNotExist:
                return self.not_found_response(
                    resource="Tailor Order",
                    message="Order not found.",
                    dev_message=f"Order {order_id} not found, not rental/sale type, or is deleted in shop {self.shop_id}"
                )

            if order.status == "completed":
                return self.validation_error_response(
                    errors={"status": ["Cannot modify a completed order."]},
                    message="Cannot modify a completed order.",
                    dev_message=f"TailorOrder {order_id} status=completed.",
                )

            # Get current values as integers
            current_paid = int(order.paid_amount)
            total_customer_charge = int(order.customer_total_charge)
            discount_amount = int(order.discount_amount)
            remaining_balance = total_customer_charge - discount_amount - current_paid

            # Validate payment doesn't exceed remaining balance
            if payment_amount > remaining_balance:
                return self.validation_error_response(
                    errors={"amount": [f"Payment amount cannot exceed remaining balance of ₹{remaining_balance}."]},
                    message=f"Payment amount cannot exceed remaining balance of ₹{remaining_balance}.",
                    dev_message=f"Payment {payment_amount} exceeds remaining balance {remaining_balance}"
                )

            # Handle edge case: zero remaining balance
            if remaining_balance <= 0:
                return self.validation_error_response(
                    errors={"payment": ["This order is already fully paid."]},
                    message="This order is already fully paid.",
                    dev_message=f"Attempted payment on fully paid order. Paid: {current_paid}, Total: {total_customer_charge}"
                )

            # Update order paid amount (save as Decimal but calculate as int)
            new_paid_amount = current_paid + payment_amount
            order.paid_amount = Decimal(str(new_paid_amount))
            order.save(update_fields=['paid_amount'])

            # Create payment record (save as integer)
            try:
                payment_record = CustomerPayment.objects.create(
                    order=order,
                    payment_amount=payment_amount,
                    payment_method=payment_method,
                    account=payment_account,
                    **({"created_at": payment_date} if payment_date else {}),
                )
            except Exception as e:
                logger.error(f"Failed to create CustomerPayment record: {str(e)}", exc_info=True)
                raise Exception("Failed to record payment transaction.")

            # Create ledger entry (new flow)
            if payment_account:
                try:
                    from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
                    _create_ledger_entries(
                        payments=[{
                            'amount': payment_amount,
                            'direction': 'in',
                            'account': payment_account,
                            'payment_method': payment_method,
                            'reference_id': payment_record.id,
                            'created_at': payment_date if payment_date else payment_record.created_at,
                        }],
                        shop=order.shop,
                        transaction_type='tailor_order_payment',
                        reference_type='CustomerPayment',
                    )
                except Exception as e:
                    logger.error(f"Failed to create ledger entry for payment: {str(e)}", exc_info=True)
                    raise Exception("Failed to create ledger entry.")

            # Calculate new remaining balance
            new_remaining_balance = total_customer_charge - new_paid_amount
            try:
                create_activity_log(
                    shop=order.shop,
                    user=request.user,
                    instance=order,
                    action="payment_updated",
                    message=f"Payment of ₹{payment_amount} via {payment_method}."
                )
            except Exception as log_error:
                logger.exception(f"Failed to log payment activity for order {order_id}: {log_error}")

            return self.success_response(
                data={
                    "total_customer_charge": str(total_customer_charge),
                    "paid_amount": str(new_paid_amount),
                    "remaining_balance": str(new_remaining_balance)
                },
                message=f"Payment of ₹{payment_amount} recorded successfully. Remaining balance: ₹{new_remaining_balance}",
                dev_message=f"Payment updated for order {order.shop_tailor_id}"
            )

    except Exception as e:
        logger.error(f"Error updating payment for order {order_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to process payment. Please try again.",
            dev_message=f"Payment update failed: {str(e)}"
        )
