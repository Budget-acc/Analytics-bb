import logging

from django.db import transaction
from rest_framework.decorators import action
from rest_framework import serializers
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer

from sales.models import Sale, SalesPaymentDetails

logger = logging.getLogger('api_requests')


class SalePaymentMixin:

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Add payment to sale",
        description="Adds a payment against an existing sale while enforcing remaining balance limits.",
        parameters=[OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id.")],
        request=inline_serializer(
            name="SalesAddPaymentV3Request",
            fields={
                "payment_amount": serializers.FloatField(required=True, help_text="Amount to add. Must be greater than zero and not exceed balance due."),
                "payment_method": serializers.CharField(required=False, default="cash", help_text="Payment method label."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Payment added successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Invalid payment amount\n"
                    "- Payment amount must be greater than zero.\n"
                    "- Payment exceeds balance\n"
                    "- Payment amount cannot exceed balance due of ₹{balance_due}."
                )
            ),
            404: OpenApiResponse(description="Exact message: The requested resource was not found"),
            500: OpenApiResponse(description="Exact message: Unable to process payment."),
        },
        examples=[
            OpenApiExample("Request example", value={"payment_amount": 500, "payment_method": "cash"}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Payment added successfully.", "data": {"sale_id": 120, "payment_added": 500, "total_paid": 1700, "remaining_balance": 700}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Exceeds balance response", value={"status": "validation_error", "message": "Payment exceeds balance", "data": {"validation_errors": {"payment_amount": ["Payment amount cannot exceed balance due of ₹700."]}}}, response_only=True, status_codes=["400"]),
            OpenApiExample("Not found response", value={"status": "not_found", "message": "The requested resource was not found", "data": None}, response_only=True, status_codes=["404"]),
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
                    message="Invalid payment amount",
                    dev_message=f"Received payment_amount={payment_amount} for sale_id={pk}"
                )

            # Calculate balance due
            balance_due = sale.total_amount - sale.discount - sale.paid_amount
            if payment_amount > balance_due:
                return self.validation_error_response(
                    errors={"payment_amount": [f"Payment amount cannot exceed balance due of ₹{balance_due}."]},
                    message="Payment exceeds balance",
                    dev_message=f"Received payment_amount={payment_amount} for sale_id={pk}"
                )

            # Update sale paid amount
            sale.paid_amount += payment_amount
            sale.save()

            # Create payment record
            SalesPaymentDetails.objects.create(
                sale=sale,
                payment_amount=payment_amount,
                payment_method=payment_method
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
            logger.error(f"Error adding payment to sale {pk} for shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to process payment.")
