import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.serializers.withdrawal_serializer import WithdrawalReadSerializer
from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries, parse_transaction_datetime
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
    summary="Create a withdrawal entry",
    description=(
        "Records capital taken out of the business (owner drawings, partner distribution, loan "
        "repayment, etc.) from the specified account. Creates a corresponding ledger outflow "
        "entry and updates the daily balance, only if the account had sufficient available "
        "balance as of transaction_date (not the account's current/live balance) - a later "
        "deposit cannot retroactively cover an earlier backdated withdrawal. Withdrawals are "
        "excluded from Profit & Loss since they are capital "
        "transactions, not operating expenses.\n\n"
        "account_id is required to identify the source account. payment_method is never "
        "accepted from the request - it is derived automatically from the account's type "
        "(cash accounts -> 'cash', all other accounts -> 'upi'). "
        "Supply either category_id or category_name; a new category is auto-created when "
        "category_name is not found. "
        "transaction_date is the actual real-world date the withdrawal occurred - this app has "
        "no bank-feed integration, so the date may be backdated by several days relative to when "
        "the record is entered."
    ),
    request=inline_serializer(
        name="WithdrawalCreateRequest",
        fields={
            "amount": serializers.DecimalField(
                max_digits=12, decimal_places=2, required=True,
                help_text="Withdrawal amount. Must be greater than 0 and not exceed the account's available balance as of transaction_date.",
            ),
            "transaction_date": serializers.CharField(
                required=True,
                help_text=(
                    "Actual date/time the withdrawal occurred. Accepts YYYY-MM-DD, "
                    "YYYY-MM-DDTHH:MM:SS, DD-MM-YYYY, or DD-MM-YYYYTHH:MM:SS. If only a date is "
                    "given, the current server time is used. Cannot be a future date."
                ),
            ),
            "account_id": serializers.IntegerField(
                required=True,
                help_text="ID of the account the withdrawal is taken from. payment_method is derived automatically from this account's type, not accepted from the request.",
            ),
            "withdrawn_by_display_name": serializers.CharField(
                required=True, help_text="Name of the person withdrawing the funds (owner, partner).",
            ),
            "withdrawn_by_phone": serializers.CharField(required=False, allow_blank=True),
            "withdrawn_by_email": serializers.EmailField(required=False, allow_blank=True),
            "withdrawal_reason": serializers.ChoiceField(
                choices=Withdrawal.WITHDRAWAL_REASON_CHOICES, required=False,
                help_text="Defaults to 'owner_drawings' if omitted.",
            ),
            "purpose": serializers.CharField(required=False, allow_blank=True, help_text="Free-text purpose, e.g. 'House rent'."),
            "category_id": serializers.IntegerField(required=False, allow_null=True),
            "category_name": serializers.CharField(required=False, allow_blank=False),
            "reference_number": serializers.CharField(required=False, allow_blank=True),
            "notes": serializers.CharField(required=False, allow_blank=True),
            "status": serializers.ChoiceField(
                choices=Withdrawal.STATUS_CHOICES, required=False,
                help_text="Defaults to 'completed'. Only 'completed' withdrawals post a ledger entry / are balance-checked.",
            ),
            "is_opening_entry": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Withdrawal recorded successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Possible messages:\n"
                "- Account is required.\n"
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
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating the withdrawal."),
    },
    examples=[
        OpenApiExample(
            "Create withdrawal",
            value={
                "amount": 20000,
                "transaction_date": "2026-07-20",
                "account_id": 3,
                "withdrawn_by_display_name": "Anil V",
                "withdrawal_reason": "owner_drawings",
                "purpose": "House rent",
            },
            request_only=True,
        ),
    ],
)
def create(self, request):
    try:
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        # payment_method is never accepted from the request - it is always
        # derived from the resolved account's account_type below.
        data.pop('payment_method', None)

        if not data.get('account_id'):
            return self.validation_error_response(
                errors={'account_id': ['Account is required.']},
                message='Account is required.',
            )

        account, payment_method, account_errors = resolve_account_and_payment_method(data, self.shop_id)
        if account_errors:
            return self.validation_error_response(
                errors=account_errors,
                message=get_first_validation_error_message(
                    account_errors, 'Please check your withdrawal details and try again.'
                ),
            )

        category, category_errors = resolve_withdrawal_category(data, self.shop_id)
        if category_errors:
            return self.validation_error_response(
                errors=category_errors,
                message=get_first_validation_error_message(
                    category_errors, 'Please check your category details and try again.'
                ),
            )

        amount_raw = data.get('amount')
        try:
            amount = Decimal(str(amount_raw))
        except (TypeError, ValueError, InvalidOperation):
            amount = None
        if amount is None or amount <= 0:
            return self.validation_error_response(
                errors={'amount': ['Amount must be greater than 0.']},
                message='Amount must be greater than 0.',
            )

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

        withdrawn_by_display_name = (data.get('withdrawn_by_display_name') or '').strip()
        if not withdrawn_by_display_name:
            return self.validation_error_response(
                errors={'withdrawn_by_display_name': ['Withdrawn-by name is required.']},
                message='Withdrawn-by name is required.',
            )

        withdrawal_reason = data.get('withdrawal_reason') or 'owner_drawings'
        if withdrawal_reason not in dict(Withdrawal.WITHDRAWAL_REASON_CHOICES):
            return self.validation_error_response(
                errors={'withdrawal_reason': ['Please select a valid withdrawal reason.']},
                message='Please select a valid withdrawal reason.',
            )

        status_value = data.get('status') or 'completed'
        if status_value not in dict(Withdrawal.STATUS_CHOICES):
            return self.validation_error_response(
                errors={'status': ['Please select a valid status.']},
                message='Please select a valid status.',
            )

        with transaction.atomic():
            if status_value == 'completed':
                # Lock the account row first so two concurrent withdrawal
                # requests against the same account serialize instead of
                # both reading a stale balance. Balance is checked as of
                # transaction_date, not the account's live/current total,
                # since a later deposit cannot retroactively cover an
                # earlier backdated withdrawal.
                available = get_available_balance_as_of(account.id, parsed_datetime.date(), lock_for_update=True)
                if amount > available:
                    date_label = parsed_datetime.strftime('%d-%m-%Y')
                    return self.validation_error_response(
                        errors={'amount': [f'Insufficient balance in this account as of {date_label}. Available balance: ₹{available}.']},
                        message=f'Insufficient balance in this account as of {date_label}. Available balance: ₹{available}.',
                    )

            withdrawal = Withdrawal.objects.create(
                shop_id=self.shop_id,
                created_by=request.user,
                account=account,
                withdrawn_by_display_name=withdrawn_by_display_name,
                withdrawn_by_phone=(data.get('withdrawn_by_phone') or None),
                withdrawn_by_email=(data.get('withdrawn_by_email') or None),
                withdrawal_reason=withdrawal_reason,
                purpose=(data.get('purpose') or None),
                payment_method=payment_method,
                category=category,
                amount=amount,
                transaction_date=parsed_datetime,
                reference_number=(data.get('reference_number') or None),
                notes=(data.get('notes') or None),
                status=status_value,
                is_opening_entry=bool(data.get('is_opening_entry', False)),
            )

            if status_value == 'completed':
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
            message="Withdrawal recorded successfully.",
        )

    except Exception as e:
        logger.error(f"Withdrawal create error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the withdrawal.",
            dev_message=str(e),
        )


__all__ = ["create"]
