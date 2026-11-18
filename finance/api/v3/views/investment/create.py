import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.serializers.investment_serializer import InvestmentReadSerializer
from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries, parse_transaction_datetime
from finance.models import Investment

from .helpers import (
    get_first_validation_error_message,
    resolve_account_and_payment_method,
    resolve_investment_category,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Create an investment entry",
    description=(
        "Records capital invested into the business (owner funding, partner contribution, "
        "external investment, etc.) into the specified account. Creates a corresponding ledger "
        "inflow entry and updates the daily balance. Investments are excluded from Profit & Loss "
        "since they are capital transactions, not operating income.\n\n"
        "account_id is required to identify the destination account. payment_method is never "
        "accepted from the request - it is derived automatically from the account's type "
        "(cash accounts -> 'cash', all other accounts -> 'upi'). "
        "Supply either category_id or category_name; a new category is auto-created when "
        "category_name is not found. "
        "transaction_date is the actual real-world date the investment was received - this app "
        "has no bank-feed integration, so the date may be backdated by several days relative to "
        "when the record is entered."
    ),
    request=inline_serializer(
        name="InvestmentCreateRequest",
        fields={
            "amount": serializers.DecimalField(
                max_digits=12, decimal_places=2, required=True,
                help_text="Investment amount. Must be greater than 0.",
            ),
            "transaction_date": serializers.CharField(
                required=True,
                help_text=(
                    "Actual date/time the investment was received. Accepts YYYY-MM-DD, "
                    "YYYY-MM-DDTHH:MM:SS, DD-MM-YYYY, or DD-MM-YYYYTHH:MM:SS. If only a date is "
                    "given, the current server time is used. Cannot be a future date."
                ),
            ),
            "account_id": serializers.IntegerField(
                required=True,
                help_text="ID of the account receiving the investment. payment_method is derived automatically from this account's type, not accepted from the request.",
            ),
            "investor_display_name": serializers.CharField(
                required=True, help_text="Name of the investor (owner, partner, external investor).",
            ),
            "investor_phone": serializers.CharField(required=False, allow_blank=True),
            "investor_email": serializers.EmailField(required=False, allow_blank=True),
            "investment_type": serializers.ChoiceField(
                choices=Investment.INVESTMENT_TYPE_CHOICES, required=False,
                help_text="Defaults to 'capital_contribution' if omitted.",
            ),
            "category_id": serializers.IntegerField(required=False, allow_null=True),
            "category_name": serializers.CharField(required=False, allow_blank=False),
            "reference_number": serializers.CharField(required=False, allow_blank=True),
            "notes": serializers.CharField(required=False, allow_blank=True),
            "status": serializers.ChoiceField(
                choices=Investment.STATUS_CHOICES, required=False,
                help_text="Defaults to 'completed'. Only 'completed' investments post a ledger entry.",
            ),
            "is_opening_entry": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Investment recorded successfully."),
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
                "- Investor name is required.\n"
                "- Please select a valid investment type.\n"
                "- Please select a valid status."
            )
        ),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating the investment."),
    },
    examples=[
        OpenApiExample(
            "Create investment",
            value={
                "amount": 50000,
                "transaction_date": "2026-07-20",
                "account_id": 3,
                "investor_display_name": "Anil V",
                "investment_type": "capital_contribution",
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
                    account_errors, 'Please check your investment details and try again.'
                ),
            )

        category, category_errors = resolve_investment_category(data, self.shop_id)
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

        investor_display_name = (data.get('investor_display_name') or '').strip()
        if not investor_display_name:
            return self.validation_error_response(
                errors={'investor_display_name': ['Investor name is required.']},
                message='Investor name is required.',
            )

        investment_type = data.get('investment_type') or 'capital_contribution'
        if investment_type not in dict(Investment.INVESTMENT_TYPE_CHOICES):
            return self.validation_error_response(
                errors={'investment_type': ['Please select a valid investment type.']},
                message='Please select a valid investment type.',
            )

        status_value = data.get('status') or 'completed'
        if status_value not in dict(Investment.STATUS_CHOICES):
            return self.validation_error_response(
                errors={'status': ['Please select a valid status.']},
                message='Please select a valid status.',
            )

        with transaction.atomic():
            investment = Investment.objects.create(
                shop_id=self.shop_id,
                created_by=request.user,
                account=account,
                investor_display_name=investor_display_name,
                investor_phone=(data.get('investor_phone') or None),
                investor_email=(data.get('investor_email') or None),
                investment_type=investment_type,
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
                        'amount': investment.amount,
                        'direction': 'in',
                        'account': account,
                        'payment_method': payment_method,
                        'reference_id': investment.id,
                        'created_at': parsed_datetime,
                    }],
                    shop=investment.shop,
                    transaction_type='investment',
                    reference_type='Investment',
                )

        return self.success_response(
            data=InvestmentReadSerializer(investment).data,
            message="Investment recorded successfully.",
        )

    except Exception as e:
        logger.error(f"Investment create error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the investment.",
            dev_message=str(e),
        )


__all__ = ["create"]
