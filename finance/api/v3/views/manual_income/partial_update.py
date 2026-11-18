import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.serializers.manual_income_serializer import (
    ManualIncomeReadSerializer,
    ManualIncomeWriteSerializer,
)
from finance.api.v3.utils import (
    create_ledger_entries as _create_ledger_entries,
    delete_ledger_entries as _delete_ledger_entries,
)
from finance.models import ManualIncome

from .helpers import (
    get_first_validation_error_message,
    resolve_account_and_payment_method,
    resolve_income_category,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Partially update a manual income entry",
    description=(
        "Updates one or more fields of an existing manual income entry. "
        "The corresponding ledger inflow entry and daily balance are recalculated atomically. "
        "Omitted fields retain their current values. "
        "Provide account_id or payment_method only when changing the account; otherwise the "
        "existing account is preserved. "
        "When category_name is provided and not found, a new ShopCategory is auto-created with type=income. "
        "transaction_date_time accepts both ISO 8601 format (2026-05-19T14:30:00) and DD-MM-YYYYTHH:MM:SS format (19-05-2026T14:30:00)."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH,
            required=True, description="Manual income ID.",
        ),
    ],
    request=inline_serializer(
        name="ManualIncomePartialUpdateRequest",
        fields={
            "amount": serializers.DecimalField(
                max_digits=12, decimal_places=2, required=False,
                help_text="Updated income amount. Must be greater than 0.",
            ),
            "transaction_date_time": serializers.DateTimeField(
                required=False,
                input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S'],
                help_text="Updated datetime of the income transaction. Accepted formats: ISO 8601 (2026-05-19T14:30:00) or DD-MM-YYYYTHH:MM:SS (19-05-2026T14:30:00).",
            ),
            "account_id": serializers.IntegerField(
                required=False,
                help_text="ID of the account. Omit to keep the current account.",
            ),
            "payment_method": serializers.CharField(
                required=False,
                help_text="'cash' or 'upi'. Omit to keep the current payment method.",
            ),
            "category_id": serializers.IntegerField(
                required=False, allow_null=True,
                help_text="ShopCategory ID. Takes priority over category_name.",
            ),
            "category_name": serializers.CharField(
                required=False, allow_blank=False,
                help_text="Category name. Auto-creates a new ShopCategory with type=income if not found.",
            ),
            "description": serializers.CharField(
                required=False, allow_blank=True,
                help_text="Updated free-text note.",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Manual income updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Possible messages:\n"
                "- Either account or payment method must be provided\n"
                "- Invalid account\n"
                "- Account is inactive\n"
                "- No active cash account found for this shop.\n"
                "- No active bank account found for this shop.\n"
                "- Invalid payment method. Use cash or upi.\n"
                "- Invalid category_id\n"
                "- amount: Must be a positive value.\n"
                "- transaction_date_time: Accepted formats: ISO 8601 (2026-05-19T14:30:00) or DD-MM-YYYYTHH:MM:SS (19-05-2026T14:30:00)."
            )
        ),
        404: OpenApiResponse(description="Exact message: Manual income not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating the manual income."),
    },
)
def partial_update(self, request, pk=None):
    try:
        try:
            income = ManualIncome.objects.select_related('account').get(
                pk=pk, shop_id=self.shop_id
            )
        except ManualIncome.DoesNotExist:
            return self.not_found_response(
                resource="ManualIncome",
                message="Manual income not found.",
                dev_message=f"ManualIncome with id {pk} not found for shop {self.shop_id}.",
            )

        data = request.data.copy()

        # Preserve existing account when not supplied in PATCH body
        if 'account_id' not in data and 'payment_method' not in data:
            if income.account_id:
                data['account_id'] = income.account_id
            elif income.payment_method:
                data['payment_method'] = income.payment_method

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

        serializer = ManualIncomeWriteSerializer(income, data=data, partial=True)
        if serializer.is_valid():
            with transaction.atomic():
                _delete_ledger_entries(
                    reference_ids=[income.id],
                    reference_type='ManualIncome',
                )
                updated_income = serializer.save()
                _create_ledger_entries(
                    payments=[{
                        'amount': updated_income.amount,
                        'direction': 'in',
                        'account': account,
                        'payment_method': payment_method,
                        'reference_id': updated_income.id,
                        'created_at': updated_income.transaction_date_time,
                    }],
                    shop=updated_income.shop,
                    transaction_type='manual_income',
                    reference_type='ManualIncome',
                )

            return self.success_response(
                message="Manual income updated successfully.",
            )

        return self.validation_error_response(
            errors=serializer.errors,
            message=get_first_validation_error_message(
                serializer.errors, 'Please check your income details and try again.'
            ),
        )

    except Exception as e:
        logger.error(f"ManualIncome partial_update error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating the manual income.",
            dev_message=str(e),
        )
