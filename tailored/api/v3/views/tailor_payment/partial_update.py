import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.utils import (
    create_ledger_entries as _create_ledger_entries,
    delete_ledger_entries as _delete_ledger_entries,
)
from tailored.api.v3.serializers.tailor_payment import (
    TailorPaymentReadSerializer,
    TailorPaymentWriteSerializer,
)
from tailored.models import Tailor, TailorPayment

from .helpers import (
    get_first_validation_error_message,
    resolve_account_and_payment_method,
    resolve_tailor_payment_category,
    validate_remaining_payable,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Partially update a tailor payment",
    description=(
        "Updates one or more fields of an existing tailor payment.\n\n"
        "Purpose: Lets shop staff correct a mistaken payment entry (wrong amount, date, "
        "account, or notes) without deleting and recreating it.\n\n"
        "Context: Used from the tailor payment list/history screen's edit action. Any field "
        "omitted from the request body keeps its current value. The corresponding ledger "
        "outflow entry is deleted and recreated (and the daily balance recalculated) "
        "atomically so accounting data stays consistent with the new values. The updated "
        "amount must not exceed the tailor's remaining pending amount, excluding this "
        "payment's own current amount (so editing a payment's amount up to its previous "
        "value plus the currently remaining pending amount is allowed).\n\n"
        "Accepted inputs:\n"
        "Path parameters — Required:\n"
        "- pk (integer): the tailor payment id to update.\n\n"
        "Request body — all optional (omitted fields keep their current value):\n"
        "- amount (decimal): new payment amount.\n"
        "- transaction_date_time (datetime): new transaction date/time.\n"
        "- account_id (integer): new payout account.\n"
        "- category_id (integer): new category. Takes priority over category_name.\n"
        "- category_name (string): new category name; auto-creates a new ShopCategory "
        "(type=expense) if not found.\n"
        "- notes (string): new free-text note."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True,
            description=(
                "Purpose: Identifies which tailor payment record to update. "
                "Location: URL path parameter. "
                "Format/Type: integer (TailorPayment id). "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="TailorPaymentPartialUpdateRequest",
        fields={
            "amount": serializers.DecimalField(
                max_digits=12, decimal_places=2, required=False,
                help_text=(
                    "Purpose: Updated payment amount. "
                    "Location: request body. "
                    "Format/Type: decimal, up to 12 digits with 2 decimal places. "
                    "Optional — omit to keep the existing amount. Must be greater than 0 and "
                    "must not exceed the tailor's remaining pending amount (excluding this "
                    "payment's own current amount)."
                ),
            ),
            "transaction_date_time": serializers.DateTimeField(
                required=False,
                input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S'],
                help_text=(
                    "Purpose: Updated date/time the payment was actually made; re-dates the "
                    "ledger entry and daily balance accordingly. "
                    "Location: request body. "
                    "Format/Type: datetime string — ISO 8601 (e.g. 2026-05-20T10:00:00) or "
                    "DD-MM-YYYYTHH:MM:SS (e.g. 20-05-2026T10:00:00). "
                    "Optional — omit to keep the existing date/time."
                ),
            ),
            "account_id": serializers.IntegerField(
                required=False,
                help_text=(
                    "Purpose: Updated payout account; payment_method (cash/upi) is re-derived "
                    "from the new account's account_type. "
                    "Location: request body. "
                    "Format/Type: integer (Account id, must belong to the active shop and be active). "
                    "Optional — omit to keep the existing account."
                ),
            ),
            "category_id": serializers.IntegerField(
                required=False, allow_null=True,
                help_text=(
                    "Purpose: Updated expense category. Takes priority over category_name when "
                    "both are provided. "
                    "Location: request body. "
                    "Format/Type: integer (ShopCategory id, must belong to the active shop and be active). "
                    "Optional — omit to keep the existing category."
                ),
            ),
            "category_name": serializers.CharField(
                required=False, allow_blank=False,
                help_text=(
                    "Purpose: Updated category name. Auto-creates a new ShopCategory with "
                    "type=expense if not found. Ignored if category_id is also provided. "
                    "Location: request body. "
                    "Format/Type: string. "
                    "Optional — omit to keep the existing category."
                ),
            ),
            "notes": serializers.CharField(
                required=False, allow_blank=True,
                help_text=(
                    "Purpose: Updated free-text note about this payment. "
                    "Location: request body. "
                    "Format/Type: string. "
                    "Optional — omit to keep the existing notes."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Tailor payment updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Either account or payment method must be provided\n"
                "- Invalid account\n"
                "- Account is inactive\n"
                "- No active cash account found for this shop.\n"
                "- No active bank account found for this shop.\n"
                "- Invalid payment method. Use cash or upi.\n"
                "- Invalid category.\n"
                "- Invalid amount.\n"
                "- Amount must be greater than zero.\n"
                "- Payment amount exceeds the tailor's remaining pending amount (X).\n"
                "- Please check your payment details and try again."
            )
        ),
        404: OpenApiResponse(description="Exact message: Tailor payment not found."),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while updating the tailor payment."),
    },
)
def partial_update(self, request, pk=None):
    try:
        try:
            payment = TailorPayment.objects.select_related('account', 'tailor', 'category').get(
                pk=pk, shop_id=self.shop_id
            )
        except TailorPayment.DoesNotExist:
            return self.not_found_response(
                resource="TailorPayment",
                message="Tailor payment not found.",
                dev_message=f"TailorPayment with id {pk} not found for shop {self.shop_id}.",
            )

        data = request.data.copy()

        # Preserve existing account when not supplied in PATCH body
        if 'account_id' not in data and 'payment_method' not in data:
            if payment.account_id:
                data['account_id'] = payment.account_id
            elif payment.payment_method:
                data['payment_method'] = payment.payment_method

        account, payment_method, errors = resolve_account_and_payment_method(data, self.shop_id)
        if errors:
            return self.validation_error_response(
                errors=errors,
                message=get_first_validation_error_message(
                    errors, 'Please check your payment details and try again.'
                ),
            )

        # Only touch category when the client explicitly sends a change;
        # category is optional/nullable so omitting both keys entirely leaves
        # the existing value untouched via partial=True below.
        if 'category_id' in data or 'category_name' in data:
            if not data.get('category_id') and not data.get('category_name'):
                # Explicit clear requested (e.g. {"category_id": null}) -
                # resolve_tailor_payment_category treats a falsy category_id
                # the same as "not provided" and would otherwise silently
                # leave the existing category untouched instead of clearing it.
                data['category'] = None
                data.pop('category_id', None)
                data.pop('category_name', None)
            else:
                category_errors = resolve_tailor_payment_category(data, self.shop_id)
                if category_errors:
                    return self.validation_error_response(
                        errors=category_errors,
                        message=get_first_validation_error_message(
                            category_errors, 'Please check your category details and try again.'
                        ),
                    )

        amount = data.get('amount', payment.amount)
        try:
            # Use Decimal (not float) so the remaining-payable comparison below
            # never suffers binary floating-point rounding at exact boundaries.
            amount_decimal = Decimal(str(amount))
        except (TypeError, ValueError, InvalidOperation):
            return self.validation_error_response(
                errors={'amount': ['Must be a valid numeric value.']},
                message='Invalid amount.',
            )

        if amount_decimal <= 0:
            return self.validation_error_response(
                errors={'amount': ['Must be a positive value.']},
                message='Amount must be greater than zero.',
            )

        data.pop('account_id', None)
        data['account'] = account.id
        data['payment_method'] = payment_method

        serializer = TailorPaymentWriteSerializer(payment, data=data, partial=True)
        if not serializer.is_valid():
            return self.validation_error_response(
                errors=serializer.errors,
                message=get_first_validation_error_message(
                    serializer.errors, 'Please check your payment details and try again.'
                ),
            )

        with transaction.atomic():
            # Lock the tailor row for the duration of the remaining-payable
            # check + save, so two concurrent update/create requests for the
            # same tailor can't both read the same remaining balance and both
            # succeed (which would let the tailor be overpaid).
            Tailor.objects.select_for_update().get(id=payment.tailor_id)

            remaining_errors = validate_remaining_payable(
                payment.tailor_id, self.shop_id, amount_decimal, exclude_payment_id=payment.id
            )
            if remaining_errors:
                return self.validation_error_response(
                    errors=remaining_errors,
                    message=get_first_validation_error_message(
                        remaining_errors, 'Payment amount exceeds the pending amount.'
                    ),
                )

            _delete_ledger_entries(
                reference_ids=[payment.id],
                reference_type='TailorPayment',
            )
            updated_payment = serializer.save()
            _create_ledger_entries(
                payments=[{
                    'amount': updated_payment.amount,
                    'direction': 'out',
                    'account': account,
                    'payment_method': payment_method,
                    'reference_id': updated_payment.id,
                    'created_at': updated_payment.transaction_date_time,
                }],
                shop=updated_payment.shop,
                transaction_type='tailor_payment',
                reference_type='TailorPayment',
            )

        return self.success_response(
            data=TailorPaymentReadSerializer(updated_payment).data,
            message="Tailor payment updated successfully.",
        )

    except Exception as e:
        logger.error(f"TailorPayment partial_update error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating the tailor payment.",
            dev_message=str(e),
        )
