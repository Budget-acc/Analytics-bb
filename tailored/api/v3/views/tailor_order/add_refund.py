import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import action
from tailored.models import TailorOrder, TailorOrderRefund
from ERP.utils.shop_activity_logs import create_activity_log
from ERP.utils.transaction_date import parse_optional_transaction_date
from ERP.utils.refund_validation import format_rupees, validate_refund_against_paid_as_of
from finance.api.v3.utils import get_available_balance_as_of
from drf_spectacular.utils import (
    OpenApiExample, OpenApiParameter, OpenApiResponse,
    OpenApiTypes, extend_schema, inline_serializer,
)
from rest_framework import serializers

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Add refund to tailor order",
    description="Records a refund against a tailor order and deducts the refunded amount from the order's paid amount.",
    parameters=[
        OpenApiParameter(
            "order_id", OpenApiTypes.INT, OpenApiParameter.PATH,
            required=True, description="Tailor Order ID."
        ),
    ],
    request=inline_serializer(
        name="CustomWorkV3AddTailorOrderRefundRequest",
        fields={
            "amount": serializers.DecimalField(required=True, max_digits=12, decimal_places=2),
            "refund_method": serializers.CharField(
                required=False,
                help_text="Allowed values: cash, upi. Required when account_id is not provided.",
            ),
            "account_id": serializers.IntegerField(
                required=False, allow_null=True,
                help_text="Finance account ID. When provided, refund_method is inferred from the account type.",
            ),
            "refund_reason": serializers.CharField(required=False, allow_null=True),
            "date": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Purpose: Actual date (and optional time) the refund was paid out, for refunds recorded later. "
                    "Location: request body. "
                    "Format/Type: string - DD-MM-YYYY or YYYY-MM-DD, optionally with time THH:MM or THH:MM:SS "
                    "(e.g. 22-09-2026, 2026-09-22T15:30). When only a date is given, the current local time is used. "
                    "Cannot be in the future or earlier than the order date. "
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
        400: OpenApiResponse(
            description=(
                "Exact error messages:\n"
                "- Refund amount is required.\n"
                "- Refund amount must be greater than zero.\n"
                "- Invalid refund amount.\n"
                "- Invalid account selected.\n"
                "- Please select a valid refund method.\n"
                "- Cannot modify a completed order.\n"
                "- Nothing left to refund for this order.\n"
                "- Only ₹<X> can be refunded for this order.\n"
                "- Invalid refund date format.\n"
                "- Date cannot be a future date.\n"
                "- Refund date cannot be earlier than the order date.\n"
                "- No payment has been received yet, so nothing can be refunded.\n"
                "- No payment was received by <DD-MM-YYYY HH:MM>. First payment was on <DD-MM-YYYY HH:MM>.\n"
                "- Nothing to refund as of <DD-MM-YYYY HH:MM>. The ₹<paid> paid by then was already refunded.\n"
                "- Only ₹<X> can be refunded as of <DD-MM-YYYY HH:MM> (₹<paid> paid, ₹<refunded> refunded).\n"
                "- Only ₹<X> can be refunded as of <DD-MM-YYYY HH:MM>. ₹<available> was available, but later refunds already use ₹<used>.\n"
                "- Nothing can be refunded as of <DD-MM-YYYY HH:MM>. Later refunds already use the ₹<available> available.\n"
                "- Only ₹<available> is available in <account name> as of <DD-MM-YYYY HH:MM>."
            )
        ),
        404: OpenApiResponse(description="Exact message: Tailor Order not found."),
        500: OpenApiResponse(description="Exact message: Failed to record refund. Please try again."),
    },
    examples=[
        OpenApiExample(
            "Refund success",
            value={
                "status": "success",
                "message": "Refund recorded successfully.",
                "data": {
                    "refunded_amount": "500.00",
                    "updated_paid_amount": "1000.00",
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
@action(detail=False, methods=["post"], url_path=r"add-refund/(?P<order_id>[^/.]+)")
@transaction.atomic
def add_refund(self, request, *args, **kwargs):
    """
    Record a refund for a tailor order.

    URL: /tailor-order/add-refund/{order_id}/  (POST)

    - Deducts the refund amount from the order's paid_amount.
    - Supports both account-based (account_id) and method-based (refund_method) flows.
    - Uses select_for_update to prevent race conditions.
    """
    order_id = kwargs.get("order_id")

    # -------- Validate order_id --------
    try:
        order_id = int(order_id)
    except (ValueError, TypeError):
        return self.validation_error_response(
            message="Invalid order provided.",
            dev_message=f"order_id is not a valid integer: {order_id}",
        )

    # -------- Validate amount --------
    amount_raw = request.data.get("amount")
    if amount_raw is None or amount_raw == "":
        return self.validation_error_response(
            errors={"amount": ["This field is required."]},
            message="Refund amount is required.",
        )

    try:
        if isinstance(amount_raw, (int, float)):
            refund_amount = Decimal(str(amount_raw))
        else:
            refund_amount = Decimal(str(amount_raw).strip())

        if refund_amount <= 0:
            raise ValueError("non-positive")
    except Exception:
        return self.validation_error_response(
            errors={"amount": ["Provide a positive numeric refund amount."]},
            message="Invalid refund amount.",
            dev_message=f"Amount conversion failed: {amount_raw}",
        )

    # -------- Validate and synchronize refund_method / account_id --------
    refund_method = (request.data.get("refund_method") or "").strip().lower()
    account_id = request.data.get("account_id")
    allowed_methods = {"cash", "upi"}
    refund_account = None

    from finance.models import Account as _Account

    def _method_for_account(account):
        if account.account_type == "cash":
            return "cash"
        if account.account_type == "bank":
            return "upi"
        return None

    def _account_type_for_method(method):
        return "cash" if method == "cash" else "bank"

    if refund_method and refund_method not in allowed_methods:
        return self.validation_error_response(
            errors={"refund_method": [f"Allowed values: {', '.join(sorted(allowed_methods))}."]},
            message="Please select a valid refund method.",
            dev_message=f"Invalid refund_method: {refund_method}",
        )

    # Case 1: account_id provided (with or without refund_method)
    if account_id:
        try:
            refund_account = _Account.objects.get(
                id=account_id,
                shop_id=self.shop_id,
                deleted__isnull=True,
                is_active=True,
            )
        except _Account.DoesNotExist:
            return self.validation_error_response(
                errors={"account_id": ["Invalid or inactive account."]},
                message="Invalid account selected.",
            )

        inferred_method = _method_for_account(refund_account)
        if inferred_method not in allowed_methods:
            return self.validation_error_response(
                errors={"account_id": ["Selected account type is not valid for refund processing."]},
                message="Invalid account selected.",
                dev_message=f"Unsupported account_type={refund_account.account_type}",
            )

        # Case 3: both provided -> enforce consistency
        if refund_method and refund_method != inferred_method:
            return self.validation_error_response(
                errors={"refund_method": ["Provided refund_method does not match selected account type."]},
                message="Refund method and account do not match.",
                dev_message=(
                    f"refund_method={refund_method}, account_type={refund_account.account_type}, "
                    f"expected_method={inferred_method}"
                ),
            )

        refund_method = inferred_method

    # Case 2: only refund_method provided -> auto-assign matching account
    elif refund_method:
        expected_account_type = _account_type_for_method(refund_method)
        refund_account = (
            _Account.objects.filter(
                shop_id=self.shop_id,
                deleted__isnull=True,
                is_active=True,
                account_type=expected_account_type,
            )
            .order_by("-is_default", "id")
            .first()
        )
        if not refund_account:
            return self.validation_error_response(
                errors={"account_id": [f"No active {expected_account_type} account found for selected refund method."]},
                message="No matching account found for refund method.",
                dev_message=f"shop_id={self.shop_id}, refund_method={refund_method}",
            )

    else:
        return self.validation_error_response(
            errors={"refund_method": ["Either refund_method or account_id is required."]},
            message="Please select a valid refund method.",
            dev_message="Both refund_method and account_id are missing.",
        )

    # Final guard: only cash and upi are accepted refund methods.
    if refund_method not in allowed_methods:
        return self.validation_error_response(
            errors={"refund_method": ["Only 'cash' and 'upi' are allowed."]},
            message="Please select a valid refund method.",
            dev_message=f"Final validation failed for refund_method={refund_method}",
        )

    refund_reason = request.data.get("refund_reason") or None

    try:
        # -------- Fetch & lock the tailor order --------
        try:
            order = TailorOrder.objects.select_for_update().get(
                id=order_id,
                shop_id=self.shop_id,
                deleted__isnull=True,
            )
        except TailorOrder.DoesNotExist:
            return self.not_found_response(
                resource="Tailor Order",
                message="Tailor Order not found.",
                dev_message=f"TailorOrder {order_id} not found or deleted in shop {self.shop_id}.",
            )

        if order.status == "completed":
            return self.validation_error_response(
                errors={"status": ["Cannot modify a completed order."]},
                message="Cannot modify a completed order.",
                dev_message=f"TailorOrder {order_id} status=completed.",
            )

        # -------- Optional historical refund date (None -> current time) --------
        refund_date, date_error = parse_optional_transaction_date(
            request.data.get("date"), min_date=order.created_at, min_label="order"
        )
        if date_error:
            return self.validation_error_response(**date_error)

        # -------- Check refundable balance --------
        current_paid = Decimal(str(order.paid_amount or 0))

        if current_paid <= 0:
            message = "Nothing left to refund for this order."
            return self.validation_error_response(
                errors={"amount": [message]},
                message=message,
                dev_message=f"paid_amount={current_paid} for order {order_id}",
            )

        if refund_amount > current_paid:
            message = f"Only {format_rupees(current_paid)} can be refunded for this order."
            return self.validation_error_response(
                errors={"amount": [message]},
                message=message,
                dev_message=f"requested={refund_amount}, refundable={current_paid}",
            )

        # -------- Backdated refund: must be covered by payments on/before that date --------
        if refund_date:
            paid_as_of_error = validate_refund_against_paid_as_of(
                refund_amount, refund_date,
                order.payments.all(), "payment_amount",
                order.refund_entries.all(), "refunded_amount",
            )
            if paid_as_of_error:
                return self.validation_error_response(**paid_as_of_error)

        # -------- Account must cover the refund as of the refund date --------
        if refund_account:
            # Exact refund moment, so later same-day inflows cannot fund it
            balance_as_of = refund_date or timezone.now()
            available = get_available_balance_as_of(refund_account.id, balance_as_of, lock_for_update=True)
            if refund_amount > available:
                date_label = timezone.localtime(balance_as_of).strftime("%d-%m-%Y %H:%M")
                message = f"Only {format_rupees(available)} is available in {refund_account.name} as of {date_label}."
                return self.validation_error_response(
                    errors={"amount": [message]},
                    message=message,
                    dev_message=f"account_id={refund_account.id}, available={available}, requested={refund_amount}",
                )

        # -------- Create refund record --------
        refund = TailorOrderRefund.objects.create(
            order=order,
            refunded_amount=refund_amount,
            refund_method=refund_method,
            refund_reason=refund_reason,
            refunded_by=request.user,
            account=refund_account,
            **({"created_at": refund_date} if refund_date else {}),
        )

        # -------- Deduct from paid_amount --------
        order.paid_amount = current_paid - refund_amount
        order.save(update_fields=["paid_amount"])

        # -------- Ledger entry (account-based flow) --------
        if refund_account:
            from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
            _create_ledger_entries(
                payments=[{
                    "amount": float(refund_amount),
                    "direction": "out",
                    "account": refund_account,
                    "payment_method": refund_method,
                    "reference_id": refund.id,
                    "created_at": refund.created_at,
                }],
                shop=order.shop,
                transaction_type="tailor_order_refund",
                reference_type="TailorOrderRefund",
            )

        # -------- Activity log --------
        try:
            create_activity_log(
                shop=order.shop,
                user=request.user,
                instance=order,
                action="refund_added",
                message=f"Refund of ₹{refund_amount} recorded for order {order.shop_tailor_id}.",
            )
        except Exception:
            logger.exception("Failed to create activity log for tailor order refund (add)")

        return self.success_response(
            data={
                "refunded_amount": str(refund_amount),
                "updated_paid_amount": str(order.paid_amount),
            },
            message="Refund recorded successfully.",
            dev_message=f"paid_before={current_paid}, paid_after={order.paid_amount}",
        )

    except Exception as e:
        logger.error(
            f"[add_refund] Error adding refund for TailorOrder {order_id}: {str(e)}",
            exc_info=True,
        )
        return self.error_response(
            message="Failed to record refund. Please try again.",
            dev_message=str(e),
        )


__all__ = ["add_refund"]
