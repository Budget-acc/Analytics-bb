import logging
from datetime import datetime, time

from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from django.utils.timezone import make_aware
from rest_framework.decorators import action

from sales.models import Sale, SalesPaymentDetails

logger = logging.getLogger('api_requests')


class SalePaymentMixin:

    @extend_schema(
        tags=["Sales Management V4"],
        summary="Add payment to sale",
        description="Adds incremental payment to a sale using either account-based ledger flow or simple method-based flow.",
        parameters=[OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id.")],
        request=inline_serializer(
            name="SalesV4AddPaymentRequest",
            fields={
                "payment_amount": serializers.FloatField(required=True),
                "payment_method": serializers.CharField(required=False, default="cash"),
                "account_id": serializers.IntegerField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Payment added successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Invalid payment amount\n"
                    "- Payment amount must be greater than zero.\n"
                    "- Payment exceeds balance\n"
                    "- Payment amount cannot exceed balance due of ₹{balance_due}.\n"
                    "- Invalid account selected.\n"
                    "- Invalid or inactive account for this shop."
                )
            ),
            404: OpenApiResponse(description="Exact message: Sale not found."),
            500: OpenApiResponse(description="Exact message: Unable to process payment."),
        },
        examples=[
            OpenApiExample("Request example", value={"payment_amount": 500, "account_id": 2}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Payment added successfully.", "data": {"sale_id": 55, "payment_added": 500, "total_paid": 1700, "remaining_balance": 700}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Exceeds balance response", value={"status": "validation_error", "message": "Payment exceeds balance", "data": {"validation_errors": {"payment_amount": ["Payment amount cannot exceed balance due of ₹700."]}}}, response_only=True, status_codes=["400"]),
        ],
    )
    @transaction.atomic
    @action(detail=False, methods=["patch"], url_path=r"add-payment/(?P<pk>\d+)")
    def add_payment(self, request, pk=None):
        """Add payment to an existing sale"""
        try:
            shop_id = self.shop_id
            sale = Sale.objects.filter(shop_id=shop_id, id=pk).first()
            if not sale:
                return self.not_found_response("Sale not found.")

            payment_amount = request.data.get("payment_amount")
            payment_method = request.data.get("payment_method", "cash")

            if not payment_amount or payment_amount <= 0:
                return self.validation_error_response(
                    errors={"payment_amount": ["Payment amount must be greater than zero."]},
                    message="Invalid payment amount"
                )

            balance_due = sale.total_amount - sale.discount - sale.paid_amount
            if payment_amount > balance_due:
                return self.validation_error_response(
                    errors={"payment_amount": [f"Payment amount cannot exceed balance due of ₹{balance_due}."]},
                    message="Payment exceeds balance"
                )

            sale.paid_amount += payment_amount
            sale.save()

            account_id = request.data.get("account_id")
            if account_id:
                # NEW FLOW: account-based
                from finance.models import Account
                from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries

                try:
                    account = Account.objects.get(
                        id=account_id,
                        shop_id=shop_id,
                        deleted__isnull=True,
                        is_active=True,
                    )
                except Account.DoesNotExist:
                    return self.validation_error_response(
                        errors={'account_id': 'Invalid or inactive account for this shop.'},
                        message='Invalid account selected.',
                    )

                payment_method = 'cash' if account.account_type == 'cash' else 'upi'
                payment_record = SalesPaymentDetails.objects.create(
                    sale=sale,
                    payment_amount=payment_amount,
                    payment_method=payment_method,
                    account=account,
                )
                _create_ledger_entries(
                    payments=[{
                        'amount': payment_amount,
                        'direction': 'in',
                        'account': account,
                        'payment_method': payment_method,
                        'reference_id': payment_record.id,
                        'created_at': make_aware(datetime.combine(sale.sale_date, time.min)),
                    }],
                    shop=sale.shop,
                    transaction_type='sale_payment',
                    reference_type='SalesPaymentDetails',
                )
            else:
                # OLD FLOW: payment_method-based (unchanged)
                SalesPaymentDetails.objects.create(
                    sale=sale,
                    payment_amount=payment_amount,
                    payment_method=payment_method,
                )

            new_balance = sale.total_amount - sale.discount - sale.paid_amount

            return self.success_response(
                data={
                    "sale_id": sale.id,
                    "payment_added": payment_amount,
                    "total_paid": sale.paid_amount,
                    "remaining_balance": new_balance
                },
                message="Payment added successfully."
            )

        except Exception as e:
            logger.error(f"Error adding payment to sale {pk}: {e}", exc_info=True)
            return self.error_response("Unable to process payment.")
