import logging
from decimal import Decimal
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.decorators import action
from booking_v2.models import BookingV2, SecurityDepositAdjustment
from ERP.utils.shop_activity_logs import create_activity_log
from ERP.utils.transaction_date import parse_optional_transaction_date
from ERP.utils.refund_validation import validate_security_refund_after_payment
from finance.api.v3.utils import get_available_balance_as_of
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Add security adjustment",
    description=(
        "Adds security refund/deduction adjustment with optional account-based refund ledger entry. "
        "A refund or deduction can be added only after the security amount is paid: the booking's "
        "security payment must have is_paid=true (set via security_amount_is_paid) and a paid_at value; "
        "otherwise the request is rejected with 'Security amount has not been paid yet.'"
    ),
    parameters=[OpenApiParameter(name="booking_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID.")],
    request=inline_serializer(
        name="AddSecurityAdjustmentV5Request",
        fields={
            "refund_amount": serializers.DecimalField(max_digits=10, decimal_places=2, required=False),
            "deduction_amount": serializers.DecimalField(max_digits=10, decimal_places=2, required=False),
            "payment_method": serializers.CharField(required=False),
            "account_id": serializers.IntegerField(required=False),
            "refund_reason": serializers.CharField(required=False),
            "deduction_reason": serializers.CharField(required=False),
            "date": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Purpose: Actual date (and optional time) the security refund was paid out, for refunds recorded later. "
                    "Applies ONLY to the refund entry (refund_amount) and its ledger entry; the deduction entry "
                    "always uses the current time and never creates a ledger entry. Ignored when refund_amount is not sent. "
                    "Location: request body. "
                    "Format/Type: string - DD-MM-YYYY or YYYY-MM-DD, optionally with time THH:MM or THH:MM:SS "
                    "(e.g. 22-09-2026, 2026-09-22T15:30). When only a date is given, the current local time is used. "
                    "Cannot be in the future or earlier than the booking date. "
                    "The security deposit must already be paid (is_paid=true with a paid_at value - a payment "
                    "without paid_at is not refundable; there is no fallback to created_at). The refund cannot be "
                    "earlier than the security payment's paid_at date/time, and cannot exceed the security paid as "
                    "of that date/time minus security adjustments recorded by then (date-only values are compared by day). "
                    "Optional. If omitted, the current date and time is used (existing behaviour)."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Security adjustment added successfully."),
        400: OpenApiResponse(description="Exact messages include: Security deposit not available.; Booking already completed.; No amount addded.; Invalid amount.; Invalid payment method.; Payment method or account required for refund.; Invalid account selected.; Total amount exceeds security deposit.; Invalid refund date format.; Date cannot be a future date.; Refund date cannot be earlier than the booking date.; Security amount has not been paid yet.; Refund date cannot be earlier than the security payment date.; Security refund exceeds the security amount paid as of the refund date.; Insufficient balance in this account as of <DD-MM-YYYY>. Available balance: ₹<available>."),
        404: OpenApiResponse(description="Booking not found response."),
        500: OpenApiResponse(description="Exact message: Failed to create transaction"),
    },
)
@action(detail=False, methods=["post"], url_path="security-adjustment/(?P<booking_id>[^/.]+)")
@transaction.atomic
def add_security_adjustment(self, request, *args, **kwargs):

    booking_id = kwargs.get("booking_id")
    savepoint = transaction.savepoint()

    try:
        # -------- Lock Booking --------
        booking = BookingV2.objects.select_for_update().get(
            id=booking_id,
            shop_id=self.shop_id,
            deleted__isnull=True
        )

        if not booking.security_amount or booking.security_amount <= 0:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security_amount": ["No security deposit found."]},
                message="Security deposit not available."
            )

        if booking.booking_status == "completed":
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"booking_status": ["Cannot modify completed booking."]},
                message="Booking already completed."
            )

        # -------- Security must be paid before any refund / adjustment --------
        paid_security = booking.security_deposit_payments.filter(is_paid=True).first()
        if not paid_security:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security_amount": ["Security refund or adjustment can be added only after the security amount is paid."]},
                message="Security amount has not been paid yet.",
                dev_message="No security payment with is_paid=true for this booking.",
            )
        if not paid_security.paid_at:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security_amount": ["Security payment date is missing."]},
                message="Security amount has not been paid yet.",
                dev_message="Security payment is marked paid (is_paid=true) but paid_at is null.",
            )

        # -------- Input --------
        refund_amount = request.data.get("refund_amount")
        deduction_amount = request.data.get("deduction_amount")
        payment_method = request.data.get("payment_method")
        account_id = request.data.get("account_id")
        refund_reason = request.data.get("refund_reason", "").strip()
        deduction_reason = request.data.get("deduction_reason", "").strip()

        if refund_amount is None and deduction_amount is None:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"amount": ["Provide refund_amount or deduction_amount"]},
                message="No amount addded."
            )

        def parse_decimal(value):
            if value is None:
                return Decimal("0")
            try:
                val = Decimal(str(value))
                if val < 0:
                    raise ValueError
                return val
            except Exception:
                raise ValidationError("Invalid amount format")

        try:
            refund_amount = parse_decimal(refund_amount)
            deduction_amount = parse_decimal(deduction_amount)
        except ValidationError as e:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"amount": [str(e)]},
                message="Invalid amount."
            )

        # -------- Validate Payment Method --------
        if payment_method:
            payment_method = payment_method.lower().strip()
            if payment_method not in self.VALID_PAYMENT_METHODS:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"payment_method": ["Invalid payment method"]},
                    message="Invalid payment method."
                )

        # -------- Refund Must Have Payment Method OR Account ID --------
        if refund_amount > 0 and not payment_method and not account_id:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"payment_method": ["Either payment_method or account_id is required for refunds."]},
                message="Payment method or account required for refund."
            )

        # -------- Validate Account (new flow for refund) --------
        account = None
        if  account_id:
            from finance.models import Account as _Account
            try:
                account = _Account.objects.get(
                    id=account_id,
                    shop_id=self.shop_id,
                    deleted__isnull=True,
                    is_active=True,
                )
                payment_method = 'cash' if account.account_type == 'cash' else 'upi'
            except _Account.DoesNotExist:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={'account_id': ['Invalid or inactive account.']},
                    message='Invalid account selected.',
                )

        # -------- Optional historical refund date (refund entry only) --------
        refund_date = None
        if refund_amount > 0:
            refund_date, is_date_only, date_error = parse_optional_transaction_date(
                request.data.get("date"), min_date=booking.created_at, min_label="booking",
                return_date_only=True,
            )
            if date_error:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(**date_error)

            # Backdated security refund: must be covered by the security amount
            # already paid as of that date/time (Booking uses the payment's paid_at)
            if refund_date:
                security_date_error = validate_security_refund_after_payment(
                    refund_date, is_date_only, refund_amount,
                    booking.security_deposit_payments.all(),
                    booking.security_adjustments.all(),
                    paid_date_field="paid_at", owner_label="booking",
                )
                if security_date_error:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(**security_date_error)

        # -------- Calculate Existing Adjustments --------
        from django.db.models.functions import Coalesce
        existing_adjustments = SecurityDepositAdjustment.objects.select_for_update().filter(
            booking=booking
        )

        adjustment_stats = existing_adjustments.aggregate(
            total_adjusted=Coalesce(models.Sum("amount"), Decimal("0"))
        )

        total_adjusted = adjustment_stats["total_adjusted"]

        security_amount = Decimal(str(booking.security_amount))

        new_total = total_adjusted + refund_amount + deduction_amount

        if new_total > security_amount:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"amount": [
                    f"Adjustment exceeds security deposit. Available: \u20b9{security_amount - total_adjusted}"
                ]},
                message="Total amount exceeds security deposit."
            )

        # -------- Account must cover the refund as of the refund date --------
        if refund_amount > 0 and account:
            as_of_date = timezone.localtime(refund_date).date() if refund_date else timezone.localdate()
            # Timestamped refund -> exact moment; date-only -> end of that day
            is_timestamped = bool(refund_date) and not is_date_only
            balance_as_of = refund_date if is_timestamped else as_of_date
            available = get_available_balance_as_of(account.id, balance_as_of, lock_for_update=True)
            if refund_amount > available:
                transaction.savepoint_rollback(savepoint)
                date_label = (
                    timezone.localtime(refund_date).strftime("%d-%m-%Y %H:%M")
                    if is_timestamped else as_of_date.strftime("%d-%m-%Y")
                )
                message = f"Insufficient balance in this account as of {date_label}. Available balance: ₹{available}."
                return self.validation_error_response(
                    errors={"refund_amount": [message]},
                    message=message,
                )

        created_records = []

        # -------- Create Refund --------
        if refund_amount > 0:
            refund_adj = SecurityDepositAdjustment.objects.create(
                booking=booking,
                adjustment_type="refund",
                amount=refund_amount,
                payment_method=payment_method,
                account=account,
                reason=refund_reason,
                **({"created_at": refund_date} if refund_date else {}),
            )
            created_records.append(refund_adj)

            if account:
                from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
                _create_ledger_entries(
                    payments=[{
                        'amount': refund_amount,
                        'direction': 'out',
                        'account': account,
                        'payment_method': payment_method,
                        'reference_id': refund_adj.id,
                        'created_at': refund_adj.created_at,
                    }],
                    shop=booking.shop,
                    transaction_type='security_out',
                    reference_type='SecurityDepositAdjustment',
                )

        # -------- Create Deduction --------
        if deduction_amount > 0:
            created_records.append(
                SecurityDepositAdjustment.objects.create(
                    booking=booking,
                    adjustment_type="deduction",
                    amount=deduction_amount,
                    payment_method=payment_method,
                    account=account,
                    reason=deduction_reason
                )
            )

        transaction.savepoint_commit(savepoint)

        # -------- Create Activity Log --------
        try:
            log_messages = []

            if refund_amount > 0:
                log_messages.append(
                    f"Security refund of \u20b9{refund_amount:.2f} via {payment_method.upper()}."
                )

            if deduction_amount > 0:
                log_messages.append(
                    f"Security deduction of \u20b9{deduction_amount:.2f}."
                )

            if log_messages:
                create_activity_log(
                    shop=booking.shop,
                    user=request.user,
                    instance=booking,
                    action="payment_updated",
                    message=" ".join(log_messages)
                )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for security refund: {log_error}")

        return self.success_response(
            data={
                "total_adjusted": float(new_total),
                "security_amount": float(security_amount),
                "remaining": float(security_amount - new_total),
            },
            message="Security adjustment added successfully."
        )

    except BookingV2.DoesNotExist:
        transaction.savepoint_rollback(savepoint)
        return self.not_found_response(resource="Booking")

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Security adjustment creation failed:{str(e)}", exc_info=True)
        return self.error_response(message="Failed to create transaction", dev_message=str(e))


__all__ = ["add_security_adjustment"]
