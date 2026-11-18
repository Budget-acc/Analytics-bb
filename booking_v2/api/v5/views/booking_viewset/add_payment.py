import logging
from datetime import datetime as _datetime
from django.db import transaction
from django.utils.timezone import now as _now, make_aware as _make_aware, localtime as _localtime
from rest_framework.decorators import action
from booking_v2.models import BookingV2, PaymentDetailsV2
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Add booking payment",
    description="Adds a payment entry and updates booking advance/payment status. Supports account-based ledger flow.",
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID."),
    ],
    request=inline_serializer(
        name="AddBookingPaymentV5Request",
        fields={
            "amount": serializers.IntegerField(required=True),
            "payment_method": serializers.CharField(required=False),
            "account_id": serializers.IntegerField(required=False),
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
        200: OpenApiResponse(description="Exact message: Payment recorded successfully."),
        400: OpenApiResponse(description="Exact messages include: Missing payment fields.; Missing payment method.; Payment must be positive.; Invalid amount format.; Payment exceeds balance.; Invalid account selected.; Invalid payment date format."),
        404: OpenApiResponse(description="Booking not found response."),
        500: OpenApiResponse(description="Exact messages include: Payment already completed.; Something went wrong while updating booking details."),
    },
)
@action(detail=False, methods=["post"], url_path="add-payment/(?P<pk>[^/.]+)")
@transaction.atomic
def add_payment(self, request, *args, **kwargs):
    pk = self.kwargs.get("pk")
    try:
        booking = BookingV2.objects.select_for_update().get(id=pk)

        amount = request.data.get("amount")
        payment_method = request.data.get("payment_method")
        account_id = request.data.get("account_id")

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

        if not amount:
            return self.validation_error_response(
                errors={"amount": ["Amount is required."]},
                message="Missing payment fields."
            )
        if not account_id and not payment_method:
            return self.validation_error_response(
                errors={"payment": ["Either payment_method or account_id is required."]},
                message="Missing payment method."
            )

        try:
            amount = int(amount)
            if amount <= 0:
                return self.validation_error_response(
                    errors={"amount": ["Must be positive."]},
                    message="Payment must be positive."
                )
        except Exception:
            return self.validation_error_response(
                errors={"amount": ["Invalid format."]},
                message="Invalid amount format."
            )

        # Total already includes additional charges (by design)
        total_amount = booking.total_amount or 0
        discount_amount = booking.discount_amount or 0
        advance_amount = booking.advance_amount or 0

        remaining = total_amount - discount_amount - advance_amount

        if booking.payment_status or remaining <= 0:
            return self.error_response(message="Payment already completed.")

        if amount > remaining:
            return self.validation_error_response(
                errors={"amount": [f"Payment exceeds remaining balance \u20b9{remaining}."]},
                message="Payment exceeds balance."
            )

        # Record payment
        if account_id:
            # NEW FLOW: account-based
            from finance.models import Account as _Account
            from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries

            try:
                account = _Account.objects.get(
                    id=account_id,
                    shop_id=booking.shop_id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except _Account.DoesNotExist:
                return self.validation_error_response(
                    errors={'account_id': ['Invalid or inactive account.']},
                    message='Invalid account selected.',
                )

            payment_method = 'cash' if account.account_type == 'cash' else 'upi'
            payment_record = PaymentDetailsV2.objects.create(
                booking=booking,
                payment_amount=amount,
                payment_method=payment_method,
                account=account,
                **({"created_at": payment_date} if payment_date else {}),
            )
            _create_ledger_entries(
                payments=[{
                    'amount': amount,
                    'direction': 'in',
                    'account': account,
                    'payment_method': payment_method,
                    'reference_id': payment_record.id,
                    'created_at': payment_date if payment_date else payment_record.created_at,
                }],
                shop=booking.shop,
                transaction_type='booking_payment',
                reference_type='PaymentDetailsV2',
            )
        else:
            # OLD FLOW: payment_method-based
            PaymentDetailsV2.objects.create(
                booking=booking,
                payment_amount=amount,
                payment_method=payment_method.lower().strip(),
                **({"created_at": payment_date} if payment_date else {}),
            )

        # Update booking payment info
        booking.advance_amount += amount
        final_payable = total_amount - discount_amount
        booking.payment_status = booking.advance_amount >= final_payable
        booking.payment_method = payment_method
        booking.save()

        try:
            create_activity_log(
                shop=booking.shop,
                user=request.user,
                instance=booking,
                action="payment_updated",
                message=f"Payment of \u20b9{amount} added."
            )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for booking payment: {log_error}")

        return self.success_response(
            data={
                "advance_amount": booking.advance_amount,
                "payment_status": booking.payment_status,
                "remaining_balance": max(final_payable - booking.advance_amount, 0)
            },
            message="Payment recorded successfully."
        )

    except BookingV2.DoesNotExist:
        return self.booking_not_found_response(booking_id=pk)

    except Exception as e:
        logger.error(f"Booking updating error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating booking details.",
            dev_message=str(e)
        )


__all__ = ["add_payment"]
