import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
from tailored.api.v3.serializers.tailor_payment import (
    TailorPaymentReadSerializer,
    TailorPaymentWriteSerializer,
)
from tailored.models import Tailor

from .helpers import (
    get_first_validation_error_message,
    resolve_account_and_payment_method,
    resolve_tailor_payment_category,
    validate_remaining_payable,
)

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Create a tailor payment",
    description=(
        "Records a payment made by the shop to a tailor for stitching/custom-work already "
        "completed for that shop.\n\n"
        "Purpose: Lets a shop pay out a tailor's earnings incrementally (e.g. weekly settlements) "
        "instead of one lump sum, while keeping the shop's cash/bank ledger and daily balance "
        "accurate.\n\n"
        "Context: Called from the tailor payment / tailor ledger screen after a shop owner or "
        "staff decides to pay a tailor. The payment amount is capped by the tailor's remaining "
        "pending amount (total tailor_charge across the tailor's completed order items, minus "
        "the sum of all active TailorPayment records already recorded for that tailor) so a "
        "shop cannot overpay a tailor beyond what is actually owed. On success, a matching "
        "outward LedgerEntry is created and the DailyBalance for the account/date is updated "
        "atomically with the payment record.\n\n"
        "Accepted inputs:\n"
        "Request body — Required:\n"
        "- tailor_id (integer): the tailor being paid.\n"
        "- amount (decimal): payment amount. Must be > 0 and <= remaining pending amount.\n"
        "- transaction_date_time (datetime): when the payment was actually made.\n"
        "- account_id (integer): account the payment is paid out from.\n\n"
        "Request body — Optional:\n"
        "- category_id (integer): ShopCategory id. Takes priority over category_name.\n"
        "- category_name (string): category name; auto-creates a new ShopCategory "
        "(type=expense) if not found. If both category_id and category_name are omitted, "
        "the payment is automatically assigned a default 'Tailor Payment' category "
        "(created on first use per shop, reused thereafter).\n"
        "- notes (string): free-text note for this payment."
    ),
    request=inline_serializer(
        name="TailorPaymentCreateRequest",
        fields={
            "tailor_id": serializers.IntegerField(
                required=True,
                help_text=(
                    "Purpose: Identifies which tailor is being paid; used to validate the "
                    "tailor belongs to the active shop and to compute their remaining pending amount. "
                    "Location: request body. "
                    "Format/Type: integer (Tailor id). "
                    "Required."
                ),
            ),
            "amount": serializers.DecimalField(
                max_digits=12, decimal_places=2, required=True,
                help_text=(
                    "Purpose: The amount being paid to the tailor in this transaction. "
                    "Location: request body. "
                    "Format/Type: decimal, up to 12 digits with 2 decimal places (e.g. 2500.00). "
                    "Required. Must be greater than 0 and must not exceed the tailor's remaining "
                    "pending amount (total charged on completed orders minus total already paid)."
                ),
            ),
            "transaction_date_time": serializers.DateTimeField(
                required=True,
                input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S'],
                help_text=(
                    "Purpose: The date/time the payment was actually handed over to the tailor "
                    "(may be earlier than when the record is created in the app). Used to date the "
                    "ledger entry and daily balance, not the record's created_at. "
                    "Location: request body. "
                    "Format/Type: datetime string — ISO 8601 (e.g. 2026-05-19T14:30:00) or "
                    "DD-MM-YYYYTHH:MM:SS (e.g. 19-05-2026T14:30:00). "
                    "Required."
                ),
            ),
            "account_id": serializers.IntegerField(
                required=True,
                help_text=(
                    "Purpose: Identifies which shop account (cash or bank) the payout is made "
                    "from. The payment_method (cash/upi) is derived automatically from the "
                    "account's account_type — cash accounts map to 'cash', bank accounts map to 'upi'. "
                    "Location: request body. "
                    "Format/Type: integer (Account id, must belong to the active shop and be active). "
                    "Required."
                ),
            ),
            "category_id": serializers.IntegerField(
                required=False, allow_null=True,
                help_text=(
                    "Purpose: Classifies this payment under an expense category (e.g. "
                    "'Stitching Labor'). Takes priority over category_name when both are provided. "
                    "Location: request body. "
                    "Format/Type: integer (ShopCategory id, must belong to the active shop and be active). "
                    "Optional — if omitted along with category_name, defaults to a 'Tailor Payment' category."
                ),
            ),
            "category_name": serializers.CharField(
                required=False, allow_blank=False,
                help_text=(
                    "Purpose: Category name string. If no matching ShopCategory exists for the "
                    "shop, a new one is auto-created with type=expense. Ignored if category_id "
                    "is also provided. "
                    "Location: request body. "
                    "Format/Type: string. "
                    "Optional — if omitted along with category_id, defaults to a 'Tailor Payment' category."
                ),
            ),
            "notes": serializers.CharField(
                required=False, allow_blank=True,
                help_text=(
                    "Purpose: Free-text note about this payment (e.g. reason, settlement period). "
                    "Location: request body. "
                    "Format/Type: string. "
                    "Optional."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Tailor payment created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Invalid tailor.\n"
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
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating the tailor payment."),
    },
    examples=[
        OpenApiExample(
            "Create tailor payment",
            value={
                "tailor_id": 5,
                "amount": 2500,
                "transaction_date_time": "2026-05-19T14:30:00",
                "account_id": 12,
                "category_name": "Stitching Labor",
                "notes": "Weekly settlement",
            },
            request_only=True,
        ),
    ],
)
def create(self, request):
    try:
        data = request.data.copy()
        data['shop'] = self.shop_id
        data['created_by'] = request.user.id

        tailor_id = data.get('tailor_id')
        if not Tailor.objects.filter(id=tailor_id, shop_id=self.shop_id, is_deleted=False).exists():
            return self.validation_error_response(
                errors={'tailor_id': ['Invalid tailor.']},
                message='Invalid tailor.',
            )
        data['tailor'] = tailor_id

        account, payment_method, errors = resolve_account_and_payment_method(data, self.shop_id)
        if errors:
            return self.validation_error_response(
                errors=errors,
                message=get_first_validation_error_message(
                    errors, 'Please check your payment details and try again.'
                ),
            )

        # Default to a "Tailor Payment" category when the client omits both
        # category_id and category_name, instead of leaving it uncategorized.
        if not data.get('category_id') and not data.get('category_name'):
            data['category_name'] = 'Tailor Payment'

        category_errors = resolve_tailor_payment_category(data, self.shop_id)
        if category_errors:
            return self.validation_error_response(
                errors=category_errors,
                message=get_first_validation_error_message(
                    category_errors, 'Please check your category details and try again.'
                ),
            )

        amount = data.get('amount')
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
        data.pop('tailor_id', None)
        data['account'] = account.id
        data['payment_method'] = payment_method

        serializer = TailorPaymentWriteSerializer(data=data)
        if not serializer.is_valid():
            return self.validation_error_response(
                errors=serializer.errors,
                message=get_first_validation_error_message(
                    serializer.errors, 'Please check your payment details and try again.'
                ),
            )

        with transaction.atomic():
            # Lock the tailor row for the duration of the remaining-payable
            # check + save, so two concurrent payment requests for the same
            # tailor can't both read the same remaining balance and both
            # succeed (which would let the tailor be overpaid).
            Tailor.objects.select_for_update().get(id=tailor_id)

            remaining_errors = validate_remaining_payable(tailor_id, self.shop_id, amount_decimal)
            if remaining_errors:
                return self.validation_error_response(
                    errors=remaining_errors,
                    message=get_first_validation_error_message(
                        remaining_errors, 'Payment amount exceeds the pending amount.'
                    ),
                )

            payment = serializer.save()
            _create_ledger_entries(
                payments=[{
                    'amount': payment.amount,
                    'direction': 'out',
                    'account': account,
                    'payment_method': payment_method,
                    'reference_id': payment.id,
                    'created_at': payment.transaction_date_time,
                }],
                shop=payment.shop,
                transaction_type='tailor_payment',
                reference_type='TailorPayment',
            )

        return self.success_response(
            data=TailorPaymentReadSerializer(payment).data,
            message="Tailor payment created successfully.",
        )

    except Exception as e:
        logger.error(f"TailorPayment create error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the tailor payment.",
            dev_message=str(e),
        )
