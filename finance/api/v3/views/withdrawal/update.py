import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.serializers.withdrawal_serializer import WithdrawalReadSerializer
from finance.api.v3.utils import (
    create_ledger_entries as _create_ledger_entries,
    delete_ledger_entries as _delete_ledger_entries,
    parse_transaction_datetime,
)
from finance.models import Withdrawal

from .helpers import (
    get_available_balance_as_of,
    get_first_validation_error_message,
    resolve_account_and_payment_method,
    resolve_withdrawal_category,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Partially update a withdrawal entry",
    description=(
        "Updates one or more fields of an existing withdrawal entry. The corresponding ledger "
        "outflow entry and daily balance are recalculated atomically - the old ledger entry (if "
        "any) is removed and a new one is created reflecting the post-update values. Omitted "
        "fields retain their current values. When the resulting status is 'completed', the "
        "account's available balance is re-checked before saving: balance is computed as of "
        "transaction_date (not the account's live/current total), and this withdrawal's own "
        "existing ledger contribution (if any) is always excluded from that sum first, so "
        "re-validating a withdrawal's own amount/date/account never counts its own prior "
        "contribution against itself. payment_method is never accepted from the request - when "
        "account_id changes, it is re-derived from the new account's type; otherwise it is left "
        "unchanged."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Withdrawal ID."),
    ],
    request=inline_serializer(
        name="WithdrawalPartialUpdateRequest",
        fields={
            "amount": serializers.DecimalField(max_digits=12, decimal_places=2, required=False),
            "transaction_date": serializers.CharField(
                required=False,
                help_text=(
                    "Accepts YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, DD-MM-YYYY, or DD-MM-YYYYTHH:MM:SS. "
                    "If only a date is given, the current server time is used."
                ),
            ),
            "account_id": serializers.IntegerField(required=False, help_text="Provide only when changing the account; payment_method is re-derived automatically."),
            "withdrawn_by_display_name": serializers.CharField(required=False),
            "withdrawn_by_phone": serializers.CharField(required=False, allow_blank=True),
            "withdrawn_by_email": serializers.EmailField(required=False, allow_blank=True),
            "withdrawal_reason": serializers.ChoiceField(choices=Withdrawal.WITHDRAWAL_REASON_CHOICES, required=False),
            "purpose": serializers.CharField(required=False, allow_blank=True),
            "category_id": serializers.IntegerField(required=False, allow_null=True),
            "category_name": serializers.CharField(required=False, allow_blank=False),
            "reference_number": serializers.CharField(required=False, allow_blank=True),
            "notes": serializers.CharField(required=False, allow_blank=True),
            "status": serializers.ChoiceField(choices=Withdrawal.STATUS_CHOICES, required=False),
            "is_opening_entry": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Withdrawal updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Possible messages:\n"
                "- Invalid account\n"
                "- Account is inactive\n"
                "- Invalid category.\n"
                "- Amount must be greater than 0.\n"
                "- Transaction date is required.\n"
                "- Transaction date cannot be in the future.\n"
                "- Withdrawn-by name is required.\n"
                "- Please select a valid withdrawal reason.\n"
                "- Please select a valid status.\n"
                "- Insufficient balance in this account as of <transaction_date>. Available balance: ₹<available>."
            )
        ),
        404: OpenApiResponse(description="Exact message: Withdrawal not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating the withdrawal."),
    },
)
def partial_update(self, request, pk=None):
    try:
        try:
            withdrawal = Withdrawal.objects.select_related('account').get(pk=pk, shop_id=self.shop_id)
        except Withdrawal.DoesNotExist:
            return self.not_found_response(
                resource="Withdrawal",
                message="Withdrawal not found.",
                dev_message=f"Withdrawal with id {pk} not found for shop {self.shop_id}.",
            )

        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)

        # payment_method is never accepted from the request - it is always
        # re-derived from the account when account_id changes, and left
        # untouched otherwise.
        data.pop('payment_method', None)

        account_changing = 'account_id' in data
        if account_changing:
            account, payment_method, account_errors = resolve_account_and_payment_method(data, self.shop_id)
        else:
            account, payment_method, account_errors = withdrawal.account, withdrawal.payment_method, None
        if account_errors:
            return self.validation_error_response(
                errors=account_errors,
                message=get_first_validation_error_message(
                    account_errors, 'Please check your withdrawal details and try again.'
                ),
            )

        category = withdrawal.category
        if 'category_id' in data or 'category_name' in data:
            category, category_errors = resolve_withdrawal_category(data, self.shop_id)
            if category_errors:
                return self.validation_error_response(
                    errors=category_errors,
                    message=get_first_validation_error_message(
                        category_errors, 'Please check your category details and try again.'
                    ),
                )

        amount = withdrawal.amount
        if 'amount' in data:
            try:
                amount = Decimal(str(data.get('amount')))
            except (TypeError, ValueError, InvalidOperation):
                amount = None
            if amount is None or amount <= 0:
                return self.validation_error_response(
                    errors={'amount': ['Amount must be greater than 0.']},
                    message='Amount must be greater than 0.',
                )

        parsed_datetime = withdrawal.transaction_date
        if 'transaction_date' in data:
            transaction_date_raw = data.get('transaction_date')
            parsed_datetime = parse_transaction_datetime(transaction_date_raw)
            if not parsed_datetime:
                return self.validation_error_response(
                    errors={'transaction_date': ['Transaction date is required.']},
                    message='Transaction date is required.',
                )
            if parsed_datetime.date() > timezone.localdate():
                return self.validation_error_response(
                    errors={'transaction_date': ['Transaction date cannot be in the future.']},
                    message='Transaction date cannot be in the future.',
                )

        withdrawn_by_display_name = withdrawal.withdrawn_by_display_name
        if 'withdrawn_by_display_name' in data:
            withdrawn_by_display_name = (data.get('withdrawn_by_display_name') or '').strip()
            if not withdrawn_by_display_name:
                return self.validation_error_response(
                    errors={'withdrawn_by_display_name': ['Withdrawn-by name is required.']},
                    message='Withdrawn-by name is required.',
                )

        withdrawal_reason = data.get('withdrawal_reason', withdrawal.withdrawal_reason)
        if withdrawal_reason not in dict(Withdrawal.WITHDRAWAL_REASON_CHOICES):
            return self.validation_error_response(
                errors={'withdrawal_reason': ['Please select a valid withdrawal reason.']},
                message='Please select a valid withdrawal reason.',
            )

        new_status = data.get('status', withdrawal.status)
        if new_status not in dict(Withdrawal.STATUS_CHOICES):
            return self.validation_error_response(
                errors={'status': ['Please select a valid status.']},
                message='Please select a valid status.',
            )

        with transaction.atomic():
            # Lock the row for the remainder of the transaction so concurrent
            # updates to the same withdrawal (e.g. a double-submitted save)
            # serialize instead of both posting a duplicate ledger entry.
            withdrawal = Withdrawal.objects.select_for_update().get(pk=withdrawal.id)
            old_status = withdrawal.status

            if new_status == 'completed':
                # Balance is checked as of the (possibly newly-supplied)
                # transaction_date, excluding this withdrawal's own existing
                # ledger contribution (if any) - correct regardless of
                # whether the account, date, or status actually changed.
                available = get_available_balance_as_of(
                    account.id, parsed_datetime.date(), exclude_withdrawal_id=withdrawal.id, lock_for_update=True,
                )
                if amount > available:
                    date_label = parsed_datetime.strftime('%d-%m-%Y')
                    return self.validation_error_response(
                        errors={'amount': [f'Insufficient balance in this account as of {date_label}. Available balance: ₹{available}.']},
                        message=f'Insufficient balance in this account as of {date_label}. Available balance: ₹{available}.',
                    )

            if old_status == 'completed':
                _delete_ledger_entries(reference_ids=[withdrawal.id], reference_type='Withdrawal')

            withdrawal.account = account
            withdrawal.category = category
            withdrawal.withdrawn_by_display_name = withdrawn_by_display_name
            withdrawal.withdrawn_by_phone = data.get('withdrawn_by_phone', withdrawal.withdrawn_by_phone) or None
            withdrawal.withdrawn_by_email = data.get('withdrawn_by_email', withdrawal.withdrawn_by_email) or None
            withdrawal.withdrawal_reason = withdrawal_reason
            withdrawal.purpose = data.get('purpose', withdrawal.purpose) or None
            withdrawal.payment_method = payment_method
            withdrawal.amount = amount
            withdrawal.transaction_date = parsed_datetime
            withdrawal.reference_number = data.get('reference_number', withdrawal.reference_number) or None
            withdrawal.notes = data.get('notes', withdrawal.notes) or None
            withdrawal.status = new_status
            withdrawal.is_opening_entry = data.get('is_opening_entry', withdrawal.is_opening_entry)
            withdrawal.save()

            if new_status == 'completed':
                _create_ledger_entries(
                    payments=[{
                        'amount': withdrawal.amount,
                        'direction': 'out',
                        'account': account,
                        'payment_method': payment_method,
                        'reference_id': withdrawal.id,
                        'created_at': parsed_datetime,
                    }],
                    shop=withdrawal.shop,
                    transaction_type='withdrawal',
                    reference_type='Withdrawal',
                )

        return self.success_response(
            data=WithdrawalReadSerializer(withdrawal).data,
            message="Withdrawal updated successfully.",
        )

    except Exception as e:
        logger.error(f"Withdrawal partial_update error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating the withdrawal.",
            dev_message=str(e),
        )


__all__ = ["partial_update"]
