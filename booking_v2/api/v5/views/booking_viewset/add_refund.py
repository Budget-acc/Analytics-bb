import logging
from django.db import transaction
from django.db.models import Sum, Value, IntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.decorators import action
from booking_v2.models import BookingV2, RefundDetails
from ERP.utils.shop_activity_logs import create_activity_log
from ERP.utils.transaction_date import parse_optional_transaction_date
from ERP.utils.refund_validation import format_rupees, validate_refund_against_paid_as_of
from finance.api.v3.utils import get_available_balance_as_of
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Add booking refund",
    description="Records a refund for a cancelled booking and updates payable state.",
    parameters=[OpenApiParameter(name="booking_id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID.")],
    request=inline_serializer(
        name="AddBookingRefundV5Request",
        fields={
            "amount": serializers.IntegerField(required=True),
            "refund_method": serializers.CharField(required=False),
            "account_id": serializers.IntegerField(required=False),
            "refund_reason": serializers.CharField(required=False),
            "date": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Purpose: Actual date (and optional time) the refund was paid out, for refunds recorded later. "
                    "Location: request body. "
                    "Format/Type: string - DD-MM-YYYY or YYYY-MM-DD, optionally with time THH:MM or THH:MM:SS "
                    "(e.g. 22-09-2026, 2026-09-22T15:30). When only a date is given, the current local time is used. "
                    "Cannot be in the future or earlier than the booking date. "
                    "The refund amount cannot exceed the payments received at or before this exact date and time "
                    "minus refunds already recorded at or before it (payment times are compared, not just dates). "
                    "Optional. If omitted, the current date and time is used (existing behaviour). "
                    "Sets the refund's created_at and its ledger entry date."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Refund recorded successfully."),
        400: OpenApiResponse(description="Exact messages include: Refund amount is required.; Invalid refund amount.; Invalid refund method.; Invalid account selected.; Nothing left to refund for this booking.; Only ₹<X> can be refunded for this booking.; Invalid refund date format.; Date cannot be a future date.; Refund date cannot be earlier than the booking date.; No payment has been received yet, so nothing can be refunded.; No payment was received by <DD-MM-YYYY HH:MM>. First payment was on <DD-MM-YYYY HH:MM>.; Nothing to refund as of <DD-MM-YYYY HH:MM>. The ₹<paid> paid by then was already refunded.; Only ₹<X> can be refunded as of <DD-MM-YYYY HH:MM> (₹<paid> paid, ₹<refunded> refunded).; Only ₹<X> can be refunded as of <DD-MM-YYYY HH:MM>. ₹<available> was available, but later refunds already use ₹<used>.; Nothing can be refunded as of <DD-MM-YYYY HH:MM>. Later refunds already use the ₹<available> available.; Only ₹<available> is available in <account name> as of <DD-MM-YYYY HH:MM>."),
        404: OpenApiResponse(description="Exact message: Booking not found."),
        500: OpenApiResponse(description="Exact message: Failed to record refund."),
    },
)
@action(detail=False, methods=["post"], url_path=r"refund/(?P<booking_id>[^/.]+)")
@transaction.atomic
def add_refund(self, request, *args, **kwargs):
    """
    Record a refund for a cancelled booking.

    - Requires: booking belongs to active shop (self.shop_id)
    - Body: { "amount": <int>, "refund_method": "cash|upi|bank", "refund_reason": "<optional>" }
    - Creates RefundDetails and updates booking.advance_amount & payment_status.
    - Uses select_for_update to avoid race conditions when computing refundable balance.
    """
    booking_id = kwargs.get("booking_id")
    try:
        # Lock booking row
        booking = (
            BookingV2.objects
            .select_for_update()
            .select_related("shop", "user")
            .filter(id=booking_id, shop_id=self.shop_id)
            .first()
        )
        if not booking:
            return self.not_found_response(
                resource="Booking",
                message="Booking not found."
            )

        # Only allow refunds for cancelled bookings (explicit requirement)
        # if (booking.delivery_status or "").lower() != "cancelled":
        #     return self.validation_error_response(
        #         errors={"delivery_status": ["Refunds are allowed only for cancelled bookings."]},
        #         message="Refunds are allowed only after booking is cancelled."
        #     )

        # Parse and validate amount
        amount_raw = request.data.get("amount")
        if amount_raw is None:
            return self.validation_error_response(
                errors={"amount": ["This field is required."]},
                message="Refund amount is required."
            )
        try:
            amount = int(amount_raw)
            if amount <= 0:
                raise ValueError("non-positive")
        except Exception:
            return self.validation_error_response(
                errors={"amount": ["Provide a positive integer amount in rupees."]},
                message="Invalid refund amount."
            )

        # Validate refund_method or account_id
        refund_method = (request.data.get("refund_method") or "").lower()
        account_id = request.data.get("account_id")
        allowed_methods = {"cash", "upi", "bank"}

        refund_account = None
        if account_id:
            # NEW FLOW: account-based
            from finance.models import Account as _Account
            try:
                refund_account = _Account.objects.get(
                    id=account_id,
                    shop_id=self.shop_id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except _Account.DoesNotExist:
                return self.validation_error_response(
                    errors={'account_id': ['Invalid or inactive account.']},
                    message='Invalid account selected.',
                )
            refund_method = 'cash' if refund_account.account_type == 'cash' else 'upi'
        elif refund_method not in allowed_methods:
            return self.validation_error_response(
                errors={"refund_method": [f"Either account_id or refund_method ({', '.join(sorted(allowed_methods))}) is required."]},
                message="Invalid refund method."
            )

        refund_reason = request.data.get("refund_reason") or None

        # Optional historical refund date (None -> existing current-time behaviour)
        refund_date, date_error = parse_optional_transaction_date(
            request.data.get("date"), min_date=booking.created_at, min_label="booking"
        )
        if date_error:
            return self.validation_error_response(**date_error)

        current_advance = int(booking.advance_amount or 0)
        refundable_balance = current_advance

        if refundable_balance <= 0:
            message = "Nothing left to refund for this booking."
            return self.validation_error_response(
                errors={"amount": [message]},
                message=message,
                dev_message=f"paid amount={current_advance}, refundable_balance={refundable_balance}"
            )

        if amount > refundable_balance:
            message = f"Only {format_rupees(refundable_balance)} can be refunded for this booking."
            return self.validation_error_response(
                errors={"amount": [message]},
                message=message,
                dev_message=f"requested={amount}, refundable_balance={refundable_balance}"
            )

        # Backdated refund: must be covered by payments received on/before that date
        if refund_date:
            paid_as_of_error = validate_refund_against_paid_as_of(
                amount, refund_date,
                booking.payments.all(), "payment_amount",
                booking.refunds.all(), "refunded_amount",
            )
            if paid_as_of_error:
                return self.validation_error_response(**paid_as_of_error)

        # Account must hold enough balance as of the refund date to pay it out
        if refund_account:
            # Exact refund moment, so later same-day inflows cannot fund it
            balance_as_of = refund_date or timezone.now()
            available = get_available_balance_as_of(refund_account.id, balance_as_of, lock_for_update=True)
            if amount > available:
                date_label = timezone.localtime(balance_as_of).strftime("%d-%m-%Y %H:%M")
                message = f"Only {format_rupees(available)} is available in {refund_account.name} as of {date_label}."
                return self.validation_error_response(
                    errors={"amount": [message]},
                    message=message,
                    dev_message=f"account_id={refund_account.id}, available={available}, requested={amount}",
                )

        # Create refund record and update booking atomically
        refund = RefundDetails.objects.create(
            booking=booking,
            refunded_amount=amount,
            refund_method=refund_method,
            refund_reason=refund_reason,
            refunded_by=request.user,
            account=refund_account,
            **({"created_at": refund_date} if refund_date else {}),
        )

        booking.advance_amount = current_advance - amount
        total_amount = int(booking.total_amount or 0)
        discount_amount = int(booking.discount_amount or 0)
        final_payable = total_amount - discount_amount
        booking.payment_status = booking.advance_amount >= final_payable
        booking.save(update_fields=["advance_amount", "payment_status"])

        if refund_account:
            from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
            _create_ledger_entries(
                payments=[{
                    'amount': amount,
                    'direction': 'out',
                    'account': refund_account,
                    'payment_method': refund_method,
                    'reference_id': refund.id,
                    'created_at': refund.created_at,
                }],
                shop=booking.shop,
                transaction_type='refund',
                reference_type='RefundDetails',
            )

        # Activity log (best-effort)
        try:
            create_activity_log(
                shop=booking.shop,
                user=request.user,
                instance=booking,
                action="refund_added",
                message=f"Refund of \u20b9{amount} recorded."
            )
        except Exception:
            logger.exception("Failed to create activity log for refund")

        return self.success_response(
            data={
                "refund_id": refund.id,
                "booking_id": booking.id,
                "refunded_amount": amount,
                "advance_amount": booking.advance_amount,
                "payment_status": booking.payment_status,
                "remaining_balance": max(final_payable - booking.advance_amount, 0),
            },
            message="Refund recorded successfully.",
            dev_message=f"paid amount={current_advance}, net_after_refund={current_advance - amount}"
        )

    except Exception as e:
        logger.error(f"[add_refund] Error adding refund for booking {booking_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to record refund.",
            dev_message=str(e)
        )


__all__ = ["add_refund"]
