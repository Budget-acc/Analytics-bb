import logging

from django.db import transaction
from rest_framework.exceptions import PermissionDenied
from rest_framework import serializers
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer

from booking_v2.models import Client
from sales.models import Sale, SaleItem, SalesPaymentDetails
from shop.models import Staff
from whatsapp_integration.v3.tasks import send_sale_invoice_task

from ...utils.validators import validate_amounts, validate_sale_date, verify_variant_belongs_to_shop
from ...utils.client import get_or_create_client
from ...utils.stock import calculate_available_stock

logger = logging.getLogger('api_requests')


class SaleCreateMixin:

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Create sale",
        description="Creates a sale with client details, line items, optional stock reduction, payment, and optional invoice trigger.",
        request=inline_serializer(
            name="SalesCreateV3Request",
            fields={
                "client_id": serializers.IntegerField(required=False, help_text="Existing client id. If omitted, client_name and client_phone_1 are required."),
                "client_name": serializers.CharField(required=False, help_text="Client name used when creating/finding client."),
                "client_phone_1": serializers.CharField(required=False, help_text="Primary client phone number used when creating/finding client."),
                "client_phone_2": serializers.CharField(required=False, allow_blank=True, help_text="Optional secondary client phone."),
                "staff_id": serializers.IntegerField(required=False, help_text="Optional staff id for attribution."),
                "client_address": serializers.CharField(required=False, allow_blank=True, help_text="Optional sale address."),
                "description": serializers.CharField(required=False, allow_blank=True, help_text="Optional notes/description."),
                "sale_date": serializers.CharField(required=False, help_text="Optional sale date. Formats: YYYY-MM-DD or DD-MM-YYYY."),
                "paid_amount": serializers.FloatField(required=False, default=0, help_text="Initial paid amount."),
                "discount": serializers.FloatField(required=False, default=0, help_text="Discount amount for this sale."),
                "payment_method": serializers.CharField(required=False, default="cash", help_text="Payment method for initial payment."),
                "decrease_stock": serializers.BooleanField(required=False, default=False, help_text="If true, stock is reduced immediately."),
                "send_invoice": serializers.BooleanField(required=False, default=False, help_text="If true, asynchronous invoice task is triggered."),
                "variants": inline_serializer(
                    name="SalesCreateV3Variant",
                    many=True,
                    fields={
                        "id": serializers.IntegerField(required=True, help_text="Product variant id."),
                        "quantity": serializers.IntegerField(required=False, default=1, help_text="Quantity to sell."),
                        "amount": serializers.FloatField(required=False, help_text="Per-unit price override."),
                    },
                ),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Sale created successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Client information is required\n"
                    "- Please provide client name and phone number.\n"
                    "- Invalid client details provided.\n"
                    "- Staff not found.\n"
                    "- Staff not found for this shop.\n"
                    "- No variants provided\n"
                    "- At least one product variant is required.\n"
                    "- Missing Product variant \n"
                    "- Product variant ID is required.\n"
                    "- Invalid quantity\n"
                    "- Quantity must be greater than zero.\n"
                    "- Invalid price\n"
                    "- Price must be greater than zero.\n"
                    "- Invalid sale amounts\n"
                    "- Total sale amount must be greater than zero.\n"
                    "- Paid amount cannot be negative.\n"
                    "- Discount amount cannot be negative.\n"
                    "- Discount cannot exceed total sale amount.\n"
                    "- Paid amount cannot exceed the amount due after discount.\n"
                    "- Invalid sale date\n"
                    "- Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."
                )
            ),
            403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
            404: OpenApiResponse(
                description=(
                    "Not found errors. Exact messages can include:\n"
                    "- The information you're looking for is not available\n"
                    "- Product not found in your inventory"
                )
            ),
            500: OpenApiResponse(description="Exact message: Unable to create sale. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "client_name": "Rahul",
                    "client_phone_1": "9876543210",
                    "staff_id": 3,
                    "sale_date": "2026-05-04",
                    "discount": 100,
                    "paid_amount": 1200,
                    "payment_method": "cash",
                    "decrease_stock": True,
                    "variants": [
                        {"id": 11, "quantity": 1, "amount": 1500},
                        {"id": 15, "quantity": 1, "amount": 1000}
                    ]
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Sale created successfully.",
                    "data": {
                        "sale_id": 120,
                        "total_amount": 2500,
                        "discount": 100,
                        "final_amount": 2400,
                        "paid_amount": 1200,
                        "balance_due": 1200
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid amount response",
                value={
                    "status": "validation_error",
                    "message": "Invalid sale amounts",
                    "data": {
                        "validation_errors": {
                            "amount": ["Paid amount cannot exceed the amount due after discount."]
                        }
                    }
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Product not found response",
                value={
                    "status": "not_found",
                    "message": "Product not found in your inventory",
                    "data": None
                },
                response_only=True,
                status_codes=["404"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to create sale. Please try again.",
                    "data": None
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @transaction.atomic
    def create(self, request):
        """Create a new sale"""
        try:
            shop_id = self.shop_id
            data = request.data

            # Handle client - either by ID or create new
            client = None
            if data.get("client_id"):
                client = Client.objects.filter(id=data["client_id"], shop_id=shop_id).first()
                if not client:
                    return self.client_not_found_response()
            else:
                client_name = data.get("client_name", "").strip()
                client_phone_1 = data.get("client_phone_1")
                client_phone_2 = data.get("client_phone_2")

                if not client_name or not client_phone_1:
                    return self.validation_error_response(
                        errors={"client": ["Please provide client name and phone number."]},
                        message="Client information is required",
                        dev_message="Missing client_name or client_phone_1 in request data"
                    )

                client_data = {
                    "client_name": client_name,
                    "client_phone_1": client_phone_1,
                    "client_phone_2": client_phone_2,
                }
                client, _ = get_or_create_client(shop_id, client_data)
                if not client:
                    return self.validation_error_response(
                        errors={"client": ["Invalid client details provided."]},
                        message="Client information is required"
                    )

            # Optional: Validate staff if provided
            staff = None
            staff_id = data.get("staff_id")
            if staff_id:
                try:
                    staff = Staff.objects.get(id=staff_id, shop=shop_id)
                except Staff.DoesNotExist:
                    return self.validation_error_response(
                        errors={"staff_id": ["Staff not found for this shop."]},
                        message="Staff not found."
                    )

            # --- Step 1: Validate ALL variants first ---
            variants = data.get("variants", [])
            if not variants:
                return self.validation_error_response(
                    errors={"variants": ["At least one product variant is required."]},
                    message="No variants provided",
                    dev_message="Request attempted to create sale without product variants"
                )

            total_before_discount = 0
            decrease_stock = data.get("decrease_stock", False)
            validated_variants = []

            for v in variants:
                variant_id = v.get("id")
                if not variant_id:
                    return self.validation_error_response(
                        errors={"variants": ["Product variant ID is required."]},
                        message="Missing Product variant ",
                        dev_message="Variant ID missing in one of the variants in request"
                    )

                variant = verify_variant_belongs_to_shop(variant_id, shop_id)
                if not variant:
                    return self.product_not_found_response()

                quantity = v.get("quantity", 1)
                if quantity <= 0:
                    return self.validation_error_response(
                        errors={"variants": ["Quantity must be greater than zero."]},
                        message="Invalid quantity",
                        dev_message=f"Quantity {quantity} provided for variant {variant_id}"
                    )

                if decrease_stock:
                    available = calculate_available_stock(variant)
                    if quantity > available:
                        return self.insufficient_stock_response(
                            product_name=f"{variant.product.name} - {variant.attribute}",
                            available=available,
                            requested=quantity,
                            message="Product have upcoming bookings, cannot reduce stock"
                        )

                price = v.get("amount", variant.price or 0)
                if price <= 0:
                    return self.validation_error_response(
                        errors={"variants": ["Price must be greater than zero."]},
                        message="Invalid price",
                        dev_message=f"Price {price} provided for variant {variant_id}"
                    )

                subtotal = price * quantity
                total_before_discount += subtotal

                validated_variants.append({
                    "variant": variant,
                    "quantity": quantity,
                    "price": price,
                    "subtotal": subtotal
                })

            # --- Step 2: Validate totals before creating Sale ---
            paid_amount = data.get("paid_amount", 0)
            discount_amount = data.get("discount", 0)

            is_valid, error_msg = validate_amounts(total_before_discount, paid_amount, discount_amount)
            if not is_valid:
                return self.validation_error_response(
                    errors={"amount": [error_msg]},
                    message="Invalid sale amounts",
                    dev_message=f"Validation failed: total_before_discount={total_before_discount}, paid_amount={paid_amount}, discount_amount={discount_amount}"
                )
            sale_date, date_error = validate_sale_date(data.get("sale_date"))
            if date_error:
                return self.validation_error_response(
                    errors={"sale_date": [date_error]},
                    message="Invalid sale date",
                    dev_message=f"Provided sale_date={data.get('sale_date')}"
                )

            # --- Step 3: Create Sale only if everything is valid ---
            sale = Sale.objects.create(
                shop_id=shop_id,
                client=client,
                address=data.get("client_address"),
                description=data.get("description", ""),
                sale_date=sale_date,
                total_amount=total_before_discount,
                discount=discount_amount,
                paid_amount=paid_amount,
                decrease_stock=decrease_stock,
                staff=staff,
            )

            # --- Step 4: Create SaleItems & Update Stock ---
            for item in validated_variants:
                SaleItem.objects.create(
                    sale=sale,
                    product_variant=item["variant"],
                    quantity=item["quantity"],
                    price=item["price"],
                    subtotal=item["subtotal"]
                )
                if decrease_stock:
                    item["variant"].stock -= item["quantity"]
                    item["variant"].save(update_fields=["stock"])

            # --- Step 5: Create Payment record if needed ---
            payment_method = data.get("payment_method", "cash")
            if paid_amount > 0:
                SalesPaymentDetails.objects.create(
                    sale=sale,
                    payment_amount=paid_amount,
                    payment_method=payment_method
                )

            # --- Step 8: Optionally generate PDF & send via WhatsApp ---
            send_invoice = data.get("send_invoice", False)
            if send_invoice:
                send_sale_invoice_task.delay(sale.id)

            return self.success_response(
                data={
                    "sale_id": sale.id,
                    "total_amount": sale.total_amount,
                    "discount": sale.discount,
                    "final_amount": sale.total_amount - sale.discount,
                    "paid_amount": sale.paid_amount,
                    "balance_due": sale.total_amount - sale.discount - sale.paid_amount
                },
                message="Sale created successfully."
            )

        except Exception as e:
            logger.error(f"Error creating sale for shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to create sale. Please try again.")
