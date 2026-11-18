import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.serializers.manual_income_serializer import (
    ManualIncomeReadSerializer,
    ManualIncomeWriteSerializer,
)
from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

from .helpers import (
    get_first_validation_error_message,
    resolve_account_and_payment_method,
    resolve_income_category,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Create a manual income entry",
    description=(
        "Records an external or offline income into the specified account. "
        "Creates a corresponding ledger inflow entry and updates the daily balance. "
        "Supply either account_id or payment_method to identify the destination account. "
        "Supply either category_id or category_name; a new category is auto-created when "
        "category_name is not found. Auto-created categories are saved with type=income. "
        "transaction_date_time accepts both ISO 8601 format (2026-05-19T14:30:00) and DD-MM-YYYYTHH:MM:SS format (19-05-2026T14:30:00)."
    ),
    request=inline_serializer(
        name="ManualIncomeCreateRequest",
        fields={
            "amount": serializers.DecimalField(
                max_digits=12, decimal_places=2, required=True,
                help_text="Income amount. Must be greater than 0.",
            ),
            "transaction_date_time": serializers.DateTimeField(
                required=True,
                input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S'],
                help_text="Full datetime of the income transaction. Accepted formats: ISO 8601 (2026-05-19T14:30:00) or DD-MM-YYYYTHH:MM:SS (19-05-2026T14:30:00).",
            ),
            "account_id": serializers.IntegerField(
                required=False,
                help_text="ID of the account receiving the income. Provide account_id OR payment_method.",
            ),
            "payment_method": serializers.CharField(
                required=False,
                help_text="'cash' or 'upi'. Used to auto-select the matching account when account_id is omitted.",
            ),
            "category_id": serializers.IntegerField(
                required=False, allow_null=True,
                help_text="ShopCategory ID. Takes priority over category_name when both are provided.",
            ),
            "category_name": serializers.CharField(
                required=False, allow_blank=False,
                help_text="Category name string. Auto-creates a new ShopCategory with type=income if not found.",
            ),
            "description": serializers.CharField(
                required=False, allow_blank=True,
                help_text="Optional free-text note for this income entry.",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Manual income created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Possible messages:\n"
                "- Either account or payment method must be provided\n"
                "- Invalid account\n"
                "- Account is inactive\n"
                "- No active cash account found for this shop.\n"
                "- No active bank account found for this shop.\n"
                "- Invalid payment method. Use cash or upi.\n"
                "- Invalid category\n"
                "- amount: Must be a positive value.\n"
                "- transaction_date_time: This field is required. Accepted formats: ISO 8601 (2026-05-19T14:30:00) or DD-MM-YYYYTHH:MM:SS (19-05-2026T14:30:00)."
            )
        ),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating the manual income."),
    },
    examples=[
        OpenApiExample(
            "Create with account_id and category_name (ISO 8601 format)",
            value={
                "amount": 2500,
                "transaction_date_time": "2026-05-19T14:30:00",
                "account_id": 12,
                "category_name": "Direct Shop Income",
                "description": "Cash received from walk-in customer",
            },
            request_only=True,
        ),
        OpenApiExample(
            "Create with payment_method and category_id (DD-MM-YYYY format)",
            value={
                "amount": 1800,
                "transaction_date_time": "19-05-2026T10:00:00",
                "payment_method": "upi",
                "category_id": 5,
            },
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Manual income created successfully.",
                "data": {
                    "id": 101,
                    "transaction_date_time": "2026-05-19T14:30:00+05:30",
                    "amount": "2500.00",
                    "description": "Cash received from walk-in customer",
                    "payment_method": "cash",
                    "account_id": 12,
                    "account_name": "Cash in Hand",
                    "category_id": 7,
                    "category_name": "Direct Shop Income",
                    "created_at": "2026-05-19T14:30:01+05:30",
                },
            },
            response_only=True,
        ),
    ],
)
def create(self, request):
    try:
        data = request.data.copy()
        data['shop'] = self.shop_id
        data['created_by'] = request.user.id

        account, payment_method, errors = resolve_account_and_payment_method(data, self.shop_id)
        if errors:
            return self.validation_error_response(
                errors=errors,
                message=get_first_validation_error_message(
                    errors, 'Please check your income details and try again.'
                ),
            )

        category_errors = resolve_income_category(data, self.shop_id)
        if category_errors:
            return self.validation_error_response(
                errors=category_errors,
                message=get_first_validation_error_message(
                    category_errors, 'Please check your category details and try again.'
                ),
            )

        data.pop('account_id', None)
        data['account'] = account.id
        data['payment_method'] = payment_method

        serializer = ManualIncomeWriteSerializer(data=data)
        if serializer.is_valid():
            with transaction.atomic():
                income = serializer.save()
                _create_ledger_entries(
                    payments=[{
                        'amount': income.amount,
                        'direction': 'in',
                        'account': account,
                        'payment_method': payment_method,
                        'reference_id': income.id,
                        'created_at': income.transaction_date_time,
                    }],
                    shop=income.shop,
                    transaction_type='manual_income',
                    reference_type='ManualIncome',
                )

            return self.success_response(
                message="Manual income created successfully.",
            )

        return self.validation_error_response(
            errors=serializer.errors,
            message=get_first_validation_error_message(
                serializer.errors, 'Please check your income details and try again.'
            ),
        )

    except Exception as e:
        logger.error(f"ManualIncome create error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the manual income.",
            dev_message=str(e),
        )
