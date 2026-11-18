import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from finance.api.v3.serializers.account_transfer_serializer import (
    AccountTransferReadSerializer,
    AccountTransferWriteSerializer,
)
from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
from finance.models import AccountTransfer

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Finance Management V3"],
    summary="Create an account transfer",
    description=(
        "Transfers an amount from one account to another within the same shop. "
        "Creates two ledger entries (transfer_out on source, transfer_in on destination) "
        "and updates daily balances for both accounts atomically. "
        "The source account must have sufficient balance. "
        "transfer_date_time accepts ISO 8601 (2026-05-19T14:30:00) or DD-MM-YYYYTHH:MM:SS format."
    ),
    request=inline_serializer(
        name="AccountTransferCreateRequest",
        fields={
            "from_account_id": serializers.IntegerField(
                help_text="ID of the account to debit."
            ),
            "to_account_id": serializers.IntegerField(
                help_text="ID of the account to credit."
            ),
            "amount": serializers.DecimalField(
                max_digits=12, decimal_places=2,
                help_text="Transfer amount. Must be greater than 0 and not exceed the source account balance.",
            ),
            "transfer_date_time": serializers.DateTimeField(
                input_formats=['%Y-%m-%dT%H:%M:%S', '%d-%m-%YT%H:%M:%S'],
                help_text="Datetime of the transfer. Accepted formats: ISO 8601 or DD-MM-YYYYTHH:MM:SS.",
            ),
            "notes": serializers.CharField(
                required=False, allow_blank=True,
                help_text="Optional note for this transfer.",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Account transfer created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Possible messages:\n"
                "- Source account not found.\n"
                "- Destination account not found.\n"
                "- Source account is inactive.\n"
                "- Destination account is inactive.\n"
                "- Source and destination accounts must be different.\n"
                "- Transfer amount must be greater than zero.\n"
                "- Insufficient balance in source account. Available: {balance}."
            )
        ),
        403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while creating the transfer."),
    },
    examples=[
        OpenApiExample(
            "Create transfer",
            value={
                "from_account_id": 1,
                "to_account_id": 2,
                "amount": 5000,
                "transfer_date_time": "2026-05-25T10:00:00",
                "notes": "Moving cash to bank",
            },
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Account transfer created successfully.",
                "data": {
                    "id": 1,
                    "from_account_id": 1,
                    "from_account_name": "Cash in Hand",
                    "to_account_id": 2,
                    "to_account_name": "HDFC UPI",
                    "amount": "5000.00",
                    "transfer_date_time": "2026-05-25T10:00:00+05:30",
                    "notes": "Moving cash to bank",
                    "created_by_id": 3,
                    "created_at": "2026-05-25T10:00:01+05:30",
                },
            },
            response_only=True,
        ),
    ],
)
def create(self, request):
    try:
        serializer = AccountTransferWriteSerializer(
            data=request.data,
            context={'shop_id': self.shop_id},
        )
        if not serializer.is_valid():
            errors = serializer.errors
            first_msg = next(
                (v[0] if isinstance(v, list) else str(v) for v in errors.values()),
                'Please check your transfer details and try again.',
            )
            return self.validation_error_response(errors=errors, message=first_msg)

        data = serializer.validated_data
        from_account = data['from_account']
        to_account = data['to_account']
        amount = data['amount']
        dt = data['transfer_date_time']
        notes = data.get('notes')

        with transaction.atomic():
            transfer = AccountTransfer.objects.create(
                shop_id=self.shop_id,
                from_account=from_account,
                to_account=to_account,
                amount=amount,
                transfer_date_time=dt,
                notes=notes,
                created_by=request.user,
            )

            _create_ledger_entries(
                payments=[{
                    'amount': amount,
                    'direction': 'out',
                    'account': from_account,
                    'payment_method': 'transfer',
                    'reference_id': transfer.id,
                    'created_at': dt,
                }],
                shop=transfer.shop,
                transaction_type='transfer_out',
                reference_type='AccountTransfer',
            )

            _create_ledger_entries(
                payments=[{
                    'amount': amount,
                    'direction': 'in',
                    'account': to_account,
                    'payment_method': 'transfer',
                    'reference_id': transfer.id,
                    'created_at': dt,
                }],
                shop=transfer.shop,
                transaction_type='transfer_in',
                reference_type='AccountTransfer',
            )

        return self.success_response(
            data=AccountTransferReadSerializer(transfer).data,
            message="Account transfer created successfully.",
        )

    except Exception as e:
        logger.error(f"AccountTransfer create error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while creating the transfer.",
            dev_message=str(e),
        )
