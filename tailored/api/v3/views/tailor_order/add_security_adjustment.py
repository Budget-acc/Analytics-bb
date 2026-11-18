import logging
from decimal import Decimal, InvalidOperation
from django.db import models, transaction
from django.utils import timezone
from rest_framework.decorators import action
from tailored.models import TailorOrder, TailorOrderSecurityDepositAdjustment
from ERP.utils.shop_activity_logs import create_activity_log
from ERP.utils.transaction_date import parse_optional_transaction_date
from ERP.utils.refund_validation import validate_security_refund_after_payment
from finance.api.v3.utils import get_available_balance_as_of
from drf_spectacular.utils import (
    OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer,
)
from rest_framework import serializers

logger = logging.getLogger("api_requests")

ALLOWED_METHODS = {"cash", "upi"}

ERROR_MESSAGES = {
    "invalid_account": "Invalid or inactive account.",
    "account_mismatch": "Payment method and account type do not match.",
    "no_account_for_method": "No active account found for the selected payment method.",
    "insufficient_security": "Adjustment exceeds available security deposit balance.",
}


def _method_for_account(account):
    if account.account_type == "cash":
        return "cash"
    if account.account_type == "bank":
        return "upi"
    return None


def _account_type_for_method(method):
    return "cash" if method == "cash" else "bank"


def _resolve_account_and_method(shop_id, account_id, payment_method, block_name):
    """
    Synchronize account and payment_method for a single adjustment block.

    Rules:
    - account_id only  → infer payment_method from account type
    - payment_method only → auto-assign best matching active account
    - both provided    → validate consistency
    - neither          → return (None, None) — allowed for deducted, rejected by caller for refunded

    Returns (account_instance_or_None, resolved_payment_method_or_None, error_dict_or_None)
    """
    from finance.models import Account as _Account

    if payment_method:
        payment_method = payment_method.lower().strip()
        if payment_method not in ALLOWED_METHODS:
            return None, None, {
                f"{block_name}.payment_method": [
                    f"Allowed values: {', '.join(sorted(ALLOWED_METHODS))}."
                ]
            }

    # Case 1: account_id provided
    if account_id:
        try:
            account = _Account.objects.get(
                id=account_id,
                shop_id=shop_id,
                deleted__isnull=True,
                is_active=True,
            )
        except _Account.DoesNotExist:
            return None, None, {
                f"{block_name}.account_id": ["Invalid or inactive account."]
            }

        inferred_method = _method_for_account(account)
        if inferred_method not in ALLOWED_METHODS:
            return None, None, {
                f"{block_name}.account_id": [
                    "Selected account type is not valid for this adjustment."
                ]
            }

        if payment_method and payment_method != inferred_method:
            return None, None, {
                f"{block_name}.payment_method": [
                    "Provided payment_method does not match the selected account type."
                ]
            }

        return account, inferred_method, None

    # Case 2: payment_method only → auto-assign matching account
    if payment_method:
        from finance.models import Account as _Account
        expected_type = _account_type_for_method(payment_method)
        account = (
            _Account.objects.filter(
                shop_id=shop_id,
                deleted__isnull=True,
                is_active=True,
                account_type=expected_type,
            )
            .order_by("-is_default", "id")
            .first()
        )
        if not account:
            return None, None, {
                f"{block_name}.account_id": [
                    f"No active {expected_type} account found for the selected payment method."
                ]
            }
        return account, payment_method, None

    # Case 3: neither provided
    return None, None, None


def _parse_decimal(value):
    if value is None:
        return Decimal("0"), None
    try:
        val = Decimal(str(value))
        if val < 0:
            raise ValueError
        return val, None
    except (InvalidOperation, ValueError):
        return None, "Invalid amount format."


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Add security deposit adjustment to tailor order",
    description=(
        "Creates security deposit refund and/or deduction adjustment entries for a tailor order. "
        "Both `refunded` and `deducted` blocks can be submitted in a single request; at least one must be present. "
        "For `refunded`, a payment method or account is required. "
        "For `deducted`, account/payment details are optional — no ledger entry is created. "
        "Account and payment_method are automatically synchronized: providing one derives the other."
    ),
    parameters=[
        OpenApiParameter(
            name="order_id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description="Tailor Order ID.",
        )
    ],
    request=inline_serializer(
        name="CustomWorkV3AddSecurityAdjustmentRequest",
        fields={
            "deducted": inline_serializer(
                name="CustomWorkV3SecurityDeductedBlock",
                fields={
                    "amount": serializers.DecimalField(max_digits=10, decimal_places=2, required=True),
                    "reason": serializers.CharField(required=False, allow_blank=True),
                    "payment_method": serializers.CharField(required=False, help_text="cash or upi"),
                    "account_id": serializers.IntegerField(required=False, allow_null=True),
                },
                required=False,
            ),
            "refunded": inline_serializer(
                name="CustomWorkV3SecurityRefundedBlock",
                fields={
                    "amount": serializers.DecimalField(max_digits=10, decimal_places=2, required=True),
                    "reason": serializers.CharField(required=False, allow_blank=True),
                    "payment_method": serializers.CharField(required=False, help_text="cash or upi. Required when account_id is not provided."),
                    "account_id": serializers.IntegerField(required=False, allow_null=True),
                },
                required=False,
            ),
            "date": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Purpose: Actual date (and optional time) the security refund was paid out, for refunds recorded later. "
                    "Applies ONLY to the `refunded` entry and its ledger entry; the `deducted` entry always uses "
                    "the current time and never creates a ledger entry. Ignored when no `refunded` block is sent. "
                    "Location: request body (top level, not inside the blocks). "
                    "Format/Type: string - DD-MM-YYYY or YYYY-MM-DD, optionally with time THH:MM or THH:MM:SS "
                    "(e.g. 22-09-2026, 2026-09-22T15:30). When only a date is given, the current local time is used. "
                    "Cannot be in the future or earlier than the order date. "
                    "The security deposit must already be paid; the refund cannot be earlier than the security "
                    "payment's created_at date/time, and cannot exceed the security paid as of that date/time minus "
                    "security adjustments recorded by then (date-only values are compared by day). "
                    "Optional. If omitted, the current date and time is used (existing behaviour)."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Security adjustment added successfully."),
        400: OpenApiResponse(
            description=(
                "Exact messages include: "
                "Security deposit not available.; "
                "Order already completed.; "
                "No amount provided.; "
                "Invalid amount.; "
                "Payment method or account required for refund.; "
                "Refund method and account do not match.; "
                "Invalid account selected.; "
                "Total amount exceeds security deposit.; "
                "Invalid refund date format.; "
                "Date cannot be a future date.; "
                "Refund date cannot be earlier than the order date.; "
                "Security amount has not been paid yet.; "
                "Refund date cannot be earlier than the security payment date.; "
                "Security refund exceeds the security amount paid as of the refund date.; "
                "Insufficient balance in this account as of <DD-MM-YYYY>. Available balance: ₹<available>."
            )
        ),
        404: OpenApiResponse(description="Tailor Order not found."),
        500: OpenApiResponse(description="Exact message: Failed to create security adjustment."),
    },
)
@action(detail=False, methods=["post"], url_path=r"security-adjustment/(?P<order_id>[^/.]+)")
@transaction.atomic
def add_security_adjustment(self, request, *args, **kwargs):
    order_id = kwargs.get("order_id")
    savepoint = transaction.savepoint()

    try:
        # -------- Validate order_id --------
        try:
            order_id = int(order_id)
        except (ValueError, TypeError):
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"order_id": ["Invalid order ID."]},
                message="Invalid order provided.",
            )

        # -------- Lock Order --------
        try:
            order = TailorOrder.objects.select_for_update().get(
                id=order_id,
                shop_id=self.shop_id,
                deleted__isnull=True,
            )
        except TailorOrder.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.not_found_response(resource="Tailor Order")

        # -------- Guard: security deposit --------
        if not order.security_amount or order.security_amount <= 0:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"security_amount": ["No security deposit found."]},
                message="Security deposit not available.",
            )

        # -------- Guard: completed order --------
        if order.status == "completed":
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"status": ["Cannot modify a completed order."]},
                message="Order already completed.",
            )

        # -------- Parse blocks --------
        deducted_data = request.data.get("deducted")
        refunded_data = request.data.get("refunded")

        if not deducted_data and not refunded_data:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"amount": ["Provide a deducted or refunded block."]},
                message="No amount provided.",
            )

        # -------- Parse amounts --------
        deducted_amount, deducted_err = _parse_decimal(
            deducted_data.get("amount") if deducted_data else None
        )
        refunded_amount, refunded_err = _parse_decimal(
            refunded_data.get("amount") if refunded_data else None
        )

        if deducted_err or refunded_err:
            transaction.savepoint_rollback(savepoint)
            errors = {}
            if deducted_err:
                errors["deducted.amount"] = [deducted_err]
            if refunded_err:
                errors["refunded.amount"] = [refunded_err]
            return self.validation_error_response(errors=errors, message="Invalid amount.")

        if deducted_data and deducted_amount <= 0:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"deducted.amount": ["Amount must be greater than zero."]},
                message="Invalid amount.",
            )

        if refunded_data and refunded_amount <= 0:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"refunded.amount": ["Amount must be greater than zero."]},
                message="Invalid amount.",
            )

        # -------- Resolve account/method for deducted block --------
        deducted_account = None
        deducted_method = None
        if deducted_data:
            deducted_account, deducted_method, err = _resolve_account_and_method(
                shop_id=self.shop_id,
                account_id=deducted_data.get("account_id"),
                payment_method=deducted_data.get("payment_method"),
                block_name="deducted",
            )
            if err:
                transaction.savepoint_rollback(savepoint)
                if any("No active" in msg for msgs in err.values() for msg in msgs):
                    return self.validation_error_response(
                        errors=err,
                        message=ERROR_MESSAGES["no_account_for_method"],
                    )
                return self.validation_error_response(
                    errors=err,
                    message=ERROR_MESSAGES["invalid_account"],
                )

        # -------- Resolve account/method for refunded block --------
        refunded_account = None
        refunded_method = None
        if refunded_data:
            refunded_account, refunded_method, err = _resolve_account_and_method(
                shop_id=self.shop_id,
                account_id=refunded_data.get("account_id"),
                payment_method=refunded_data.get("payment_method"),
                block_name="refunded",
            )
            if err:
                transaction.savepoint_rollback(savepoint)
                # Surface account mismatch vs invalid account
                if "payment_method" in str(err):
                    return self.validation_error_response(
                        errors=err, message=ERROR_MESSAGES["account_mismatch"]
                    )
                if any("No active" in msg for msgs in err.values() for msg in msgs):
                    return self.validation_error_response(
                        errors=err,
                        message=ERROR_MESSAGES["no_account_for_method"],
                    )
                return self.validation_error_response(
                    errors=err,
                    message=ERROR_MESSAGES["invalid_account"],
                )

            # Refunded requires payment method or account
            if not refunded_method and not refunded_account:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"refunded.payment_method": [
                        "Either payment_method or account_id is required for refunds."
                    ]},
                    message="Payment method or account required for refund.",
                )

        # -------- Optional historical refund date (refunded entry only) --------
        refund_date = None
        if refunded_data:
            refund_date, is_date_only, date_error = parse_optional_transaction_date(
                request.data.get("date"), min_date=order.created_at, min_label="order",
                return_date_only=True,
            )
            if date_error:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(**date_error)

            # Backdated security refund: must be covered by the security amount
            # already paid as of that date/time (Tailor Order uses created_at)
            if refund_date:
                security_date_error = validate_security_refund_after_payment(
                    refund_date, is_date_only, refunded_amount,
                    order.security_payments.all(),
                    order.security_adjustments.all(),
                    paid_date_field="created_at", owner_label="order",
                )
                if security_date_error:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(**security_date_error)

        # -------- Check aggregate against security_amount --------
        from django.db.models.functions import Coalesce
        existing_total = (
            TailorOrderSecurityDepositAdjustment.objects
            .select_for_update()
            .filter(order=order)
            .aggregate(total=Coalesce(models.Sum("amount"), Decimal("0")))
        )["total"]

        security_amount = Decimal(str(order.security_amount))
        new_total = existing_total + (deducted_amount or Decimal("0")) + (refunded_amount or Decimal("0"))

        if new_total > security_amount:
            transaction.savepoint_rollback(savepoint)
            available = security_amount - existing_total
            return self.validation_error_response(
                errors={"amount": [
                    f"Adjustment exceeds security deposit. Available: ₹{available}"
                ]},
                message=ERROR_MESSAGES["insufficient_security"],
            )

        # -------- Account must cover the refund as of the refund date --------
        if refunded_data and refunded_amount > 0 and refunded_account:
            as_of_date = timezone.localtime(refund_date).date() if refund_date else timezone.localdate()
            # Timestamped refund -> exact moment; date-only -> end of that day
            is_timestamped = bool(refund_date) and not is_date_only
            balance_as_of = refund_date if is_timestamped else as_of_date
            available = get_available_balance_as_of(refunded_account.id, balance_as_of, lock_for_update=True)
            if refunded_amount > available:
                transaction.savepoint_rollback(savepoint)
                date_label = (
                    timezone.localtime(refund_date).strftime("%d-%m-%Y %H:%M")
                    if is_timestamped else as_of_date.strftime("%d-%m-%Y")
                )
                message = f"Insufficient balance in this account as of {date_label}. Available balance: ₹{available}."
                return self.validation_error_response(
                    errors={"refunded.amount": [message]},
                    message=message,
                )

        # -------- Create deducted record --------
        if deducted_data and deducted_amount > 0:
            TailorOrderSecurityDepositAdjustment.objects.create(
                order=order,
                adjustment_type="deducted",
                amount=deducted_amount,
                payment_method=deducted_method,
                account=deducted_account,
                reason=(deducted_data.get("reason") or "").strip() or None,
            )

        # -------- Create refunded record + ledger entry --------
        refund_adj = None
        if refunded_data and refunded_amount > 0:
            refund_adj = TailorOrderSecurityDepositAdjustment.objects.create(
                order=order,
                adjustment_type="refunded",
                amount=refunded_amount,
                payment_method=refunded_method,
                account=refunded_account,
                reason=(refunded_data.get("reason") or "").strip() or None,
                **({"created_at": refund_date} if refund_date else {}),
            )

            if refunded_account:
                from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
                _create_ledger_entries(
                    payments=[{
                        "amount": float(refunded_amount),
                        "direction": "out",
                        "account": refunded_account,
                        "payment_method": refunded_method,
                        "reference_id": refund_adj.id,
                        "created_at": refund_adj.created_at,
                    }],
                    shop=order.shop,
                    transaction_type="tailor_security_refund",
                    reference_type="TailorOrderSecurityDepositAdjustment",
                )

        transaction.savepoint_commit(savepoint)

        # -------- Activity log --------
        try:
            log_parts = []
            if deducted_data and deducted_amount > 0:
                log_parts.append(f"Security deduction of ₹{deducted_amount:.2f}.")
            if refunded_data and refunded_amount > 0:
                method_label = refunded_method.upper() if refunded_method else "N/A"
                log_parts.append(
                    f"Security refund of ₹{refunded_amount:.2f} via {method_label}."
                )
            if log_parts:
                create_activity_log(
                    shop=order.shop,
                    user=request.user,
                    instance=order,
                    action="payment_updated",
                    message=" ".join(log_parts),
                )
        except Exception as log_err:
            logger.exception(f"Failed to create activity log for security adjustment: {log_err}")

        return self.success_response(
            data={
                "total_adjusted": float(new_total),
                "security_amount": float(security_amount),
                "remaining": float(security_amount - new_total),
            },
            message="Security adjustment added successfully.",
        )

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(
            f"[add_security_adjustment] Failed for TailorOrder {order_id}: {str(e)}",
            exc_info=True,
        )
        return self.error_response(
            message="Failed to create security adjustment.",
            dev_message=str(e),
        )


__all__ = ["add_security_adjustment"]
