import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.serializers.investment_serializer import InvestmentReadSerializer
from finance.api.v3.utils import (
    create_ledger_entries as _create_ledger_entries,
    delete_ledger_entries as _delete_ledger_entries,
    parse_transaction_datetime,
)
from finance.models import Investment

from .helpers import (
    get_first_validation_error_message,
    resolve_account_and_payment_method,
    resolve_investment_category,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Partially update an investment entry",
    description=(
        "Updates one or more fields of an existing investment entry. The corresponding ledger "
        "inflow entry and daily balance are recalculated atomically - the old ledger entry (if "
        "any) is removed and a new one is created reflecting the post-update values. Omitted "
        "fields retain their current values. Provide account_id only when changing the account; "
        "otherwise the existing account and payment_method are preserved. payment_method is "
        "never accepted from the request - when account_id changes, it is re-derived from the "
        "new account's type."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Investment ID."),
    ],
    request=inline_serializer(
        name="InvestmentPartialUpdateRequest",
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
            "investor_display_name": serializers.CharField(required=False),
            "investor_phone": serializers.CharField(required=False, allow_blank=True),
            "investor_email": serializers.EmailField(required=False, allow_blank=True),
            "investment_type": serializers.ChoiceField(choices=Investment.INVESTMENT_TYPE_CHOICES, required=False),
            "category_id": serializers.IntegerField(required=False, allow_null=True),
            "category_name": serializers.CharField(required=False, allow_blank=False),
            "reference_number": serializers.CharField(required=False, allow_blank=True),
            "notes": serializers.CharField(required=False, allow_blank=True),
            "status": serializers.ChoiceField(choices=Investment.STATUS_CHOICES, required=False),
            "is_opening_entry": serializers.BooleanField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Investment updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Possible messages:\n"
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
        404: OpenApiResponse(description="Exact message: Investment not found."),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating the investment."),
    },
)
def partial_update(self, request, pk=None):
    try:
        try:
            investment = Investment.objects.select_related('account').get(pk=pk, shop_id=self.shop_id)
        except Investment.DoesNotExist:
            return self.not_found_response(
                resource="Investment",
                message="Investment not found.",
                dev_message=f"Investment with id {pk} not found for shop {self.shop_id}.",
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
            account, payment_method, account_errors = investment.account, investment.payment_method, None
        if account_errors:
            return self.validation_error_response(
                errors=account_errors,
                message=get_first_validation_error_message(
                    account_errors, 'Please check your investment details and try again.'
                ),
            )

        category = investment.category
        if 'category_id' in data or 'category_name' in data:
            category, category_errors = resolve_investment_category(data, self.shop_id)
            if category_errors:
                return self.validation_error_response(
                    errors=category_errors,
                    message=get_first_validation_error_message(
                        category_errors, 'Please check your category details and try again.'
                    ),
                )

        amount = investment.amount
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

        parsed_datetime = investment.transaction_date
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

        investor_display_name = investment.investor_display_name
        if 'investor_display_name' in data:
            investor_display_name = (data.get('investor_display_name') or '').strip()
            if not investor_display_name:
                return self.validation_error_response(
                    errors={'investor_display_name': ['Investor name is required.']},
                    message='Investor name is required.',
                )

        investment_type = data.get('investment_type', investment.investment_type)
        if investment_type not in dict(Investment.INVESTMENT_TYPE_CHOICES):
            return self.validation_error_response(
                errors={'investment_type': ['Please select a valid investment type.']},
                message='Please select a valid investment type.',
            )

        new_status = data.get('status', investment.status)
        if new_status not in dict(Investment.STATUS_CHOICES):
            return self.validation_error_response(
                errors={'status': ['Please select a valid status.']},
                message='Please select a valid status.',
            )

        with transaction.atomic():
            # Lock the row for the remainder of the transaction so concurrent
            # updates to the same investment (e.g. a double-submitted save)
            # serialize instead of both posting a duplicate ledger entry.
            investment = Investment.objects.select_for_update().get(pk=investment.id)
            old_status = investment.status

            if old_status == 'completed':
                _delete_ledger_entries(reference_ids=[investment.id], reference_type='Investment')

            investment.account = account
            investment.category = category
            investment.investor_display_name = investor_display_name
            investment.investor_phone = data.get('investor_phone', investment.investor_phone) or None
            investment.investor_email = data.get('investor_email', investment.investor_email) or None
            investment.investment_type = investment_type
            investment.payment_method = payment_method
            investment.amount = amount
            investment.transaction_date = parsed_datetime
            investment.reference_number = data.get('reference_number', investment.reference_number) or None
            investment.notes = data.get('notes', investment.notes) or None
            investment.status = new_status
            investment.is_opening_entry = data.get('is_opening_entry', investment.is_opening_entry)
            investment.save()

            if new_status == 'completed':
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
            message="Investment updated successfully.",
        )

    except Exception as e:
        logger.error(f"Investment partial_update error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating the investment.",
            dev_message=str(e),
        )


__all__ = ["partial_update"]
