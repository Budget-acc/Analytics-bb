import logging

from django.db import transaction
from django.utils.timezone import now as _now, localtime as _localtime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action

from purchase.models import Purchase, PurchasePayment
from ERP.utils.shop_activity_logs import create_activity_log
from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries, get_available_balance_as_of

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Add purchase payment",
    description=(
        "Records a payment against a purchase's outstanding balance (purchase_total "
        "minus paid_amount) and generates an outward ledger entry stamped with the "
        "resolved payment_date. Applicable to both purchase types. payment_date must "
        "fall on or after the purchase's purchase_date and cannot be a future date. "
        "The selected account must have sufficient available balance as of payment_date "
        "(not the account's current/live balance) to cover the payment - a later deposit "
        "cannot retroactively cover an earlier backdated payment."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purchase ID."),
    ],
    request=inline_serializer(
        name="AddPurchasePaymentV3Request",
        fields={
            "amount": serializers.DecimalField(max_digits=12, decimal_places=2, required=True),
            "account_id": serializers.IntegerField(required=True),
            "payment_date": serializers.CharField(
                required=False,
                help_text=(
                    "Optional. DD-MM-YYYY or YYYY-MM-DD, with or without THH:MM:SS. "
                    "Defaults to now() if omitted; date-only combines with the current time."
                ),
            ),
            "notes": serializers.CharField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Payment recorded successfully."),
        400: OpenApiResponse(
            description=(
                "Exact messages include: Amount is required.; Payment must be positive.; "
                "Invalid amount.; Payment exceeds outstanding balance.; "
                "Invalid account selected.; This purchase is already fully paid.; "
                "Invalid payment date format.; Payment date cannot be a future date.; "
                "Payment date cannot be earlier than the purchase date.; "
                "Insufficient balance in this account as of <payment_date>. Available balance: ₹<available>."
            )
        ),
        404: OpenApiResponse(description="Exact message: Purchase not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while recording the payment."),
    },
)
@action(detail=False, methods=["post"], url_path="add-payment/(?P<pk>[^/.]+)")
@transaction.atomic
def add_payment(self, request, *args, **kwargs):
    pk = self.kwargs.get("pk")
    try:
        purchase = Purchase.objects.select_for_update().filter(
            id=pk, shop_id=self.shop_id, deleted__isnull=True
        ).first()
        if not purchase:
            return self.not_found_response(resource="Purchase", message="Purchase not found.")

        amount_raw = request.data.get("amount")
        if not amount_raw:
            return self.validation_error_response(
                errors={"amount": ["Amount is required."]},
                message="Amount is required.",
            )

        amount, error = self._to_decimal(amount_raw, "amount")
        if error:
            return error
        if amount <= 0:
            return self.validation_error_response(
                errors={"amount": ["Must be positive."]},
                message="Payment must be positive.",
            )

        remaining = purchase.outstanding_balance
        if remaining <= 0:
            return self.error_response(message="This purchase is already fully paid.")
        if amount > remaining:
            return self.validation_error_response(
                errors={"amount": [f"Payment exceeds outstanding balance ₹{remaining}."]},
                message="Payment exceeds outstanding balance.",
            )

        account, payment_method, error = self._resolve_account(purchase.shop, request.data.get("account_id"))
        if error:
            return error

        payment_date, error = self._parse_payment_date(request.data)
        if error:
            return error

        # Frontend has no payment-time picker - it always sends the current
        # time alongside the selected payment date. So when the payment date
        # is the same calendar day as the purchase date but the (auto-sent)
        # time-of-day happens to be earlier than the purchase's recorded
        # time, that's not a real "earlier payment" - re-stamp with the
        # current time on that same date rather than rejecting. Any actual
        # earlier/later calendar date is unaffected and still validated
        # exactly as before.
        if (
            _localtime(payment_date).date() == _localtime(purchase.purchase_date).date()
            and payment_date < purchase.purchase_date
        ):
            payment_date = _now().astimezone(payment_date.tzinfo)

        if payment_date > _now():
            return self.validation_error_response(
                errors={"payment_date": ["payment_date cannot be a future date."]},
                message="Payment date cannot be a future date.",
            )
        if payment_date < purchase.purchase_date:
            return self.validation_error_response(
                errors={"payment_date": ["payment_date cannot be earlier than the purchase date."]},
                message="Payment date cannot be earlier than the purchase date.",
            )

        # Lock the account row first so two concurrent payment requests
        # against the same account serialize instead of both reading a
        # stale balance. Balance is checked as of payment_date, not the
        # account's live/current total, since a later deposit cannot
        # retroactively cover an earlier backdated payment.
        available = get_available_balance_as_of(account.id, payment_date.date(), lock_for_update=True)
        if amount > available:
            date_label = payment_date.strftime("%d-%m-%Y")
            return self.validation_error_response(
                errors={"account_id": [f"Insufficient balance in this account as of {date_label}. Available balance: ₹{available}."]},
                message=f"Insufficient balance in this account as of {date_label}. Available balance: ₹{available}.",
            )

        payment = PurchasePayment.objects.create(
            purchase=purchase,
            amount=amount,
            account=account,
            payment_method=payment_method,
            payment_date=payment_date,
            notes=request.data.get("notes") or None,
        )
        _create_ledger_entries(
            payments=[{
                "amount": amount,
                "direction": "out",
                "account": account,
                "payment_method": payment_method,
                "reference_id": payment.id,
                "created_at": payment_date,
            }],
            shop=purchase.shop,
            transaction_type="purchase_payment",
            reference_type="PurchasePayment",
        )

        purchase.refresh_from_db(fields=["paid_amount"])

        try:
            create_activity_log(
                shop=purchase.shop,
                user=request.user,
                instance=purchase,
                action="payment_updated",
                message=f"Payment of ₹{amount} added to purchase {purchase.purchase_code}.",
            )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for purchase payment: {log_error}")

        return self.success_response(
            data={
                "paid_amount": purchase.paid_amount,
                "outstanding_balance": purchase.outstanding_balance,
            },
            message="Payment recorded successfully.",
        )

    except Exception as e:
        logger.error(f"Purchase add_payment error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while recording the payment.",
            dev_message=str(e),
        )


__all__ = ["add_payment"]
