import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from sales.models import Sale, SaleItem, SalesPaymentDetails
from shop.models import Shop, Staff
from shop.api.v3.utils.tax_calculation import (
    get_active_tax_config,
    resolve_taxable_flags,
    split_inclusive_amount,
)
from ...utils.validators import validate_amounts, validate_sale_date, verify_variant_belongs_to_shop
from ...utils.stock import calculate_available_stock

logger = logging.getLogger('api_requests')


class SaleCreateMixin:

    def _get_ledger_timestamp(self, sale):
        """Use sale record timestamp when available, else fallback to current time."""
        ts = getattr(sale, 'created_at', None) or timezone.now()
        return timezone.make_aware(ts) if timezone.is_naive(ts) else ts

    @extend_schema(
        tags=["Sales Management V4"],
        summary="Create sale",
        description="Creates a phone-based sale with variants, optional stock reduction, and optional account-based payment flow.",
        request=inline_serializer(
            name="SalesV4CreateRequest",
            fields={
                "client_phone": serializers.CharField(required=False, help_text="Optional client phone in local format."),
                "client_phone_e164": serializers.CharField(required=False, help_text="Optional E.164 formatted client phone."),
                "staff_id": serializers.IntegerField(required=False),
                "client_address": serializers.CharField(required=False, allow_blank=True),
                "description": serializers.CharField(required=False, allow_blank=True),
                "sale_date": serializers.CharField(required=False, help_text="Format: YYYY-MM-DD or DD-MM-YYYY."),
                "paid_amount": serializers.FloatField(required=False, help_text="If omitted, defaults to the final payable amount (total_amount - discount, tax-inclusive)."),
                "discount": serializers.FloatField(required=False, default=0),
                "decrease_stock": serializers.BooleanField(required=False, default=False),
                "account_id": serializers.IntegerField(required=False, help_text="Optional account id for ledger-backed payment."),
                "payment_method": serializers.CharField(required=False, default="cash", help_text="Used when account_id is not supplied."),
                "send_invoice": serializers.BooleanField(required=False, default=False),
                "variants": inline_serializer(
                    name="SalesV4CreateVariant",
                    many=True,
                    fields={
                        "id": serializers.IntegerField(required=True),
                        "quantity": serializers.IntegerField(required=False, default=1),
                        "amount": serializers.FloatField(required=False),
                    },
                ),
            },
        ),
        responses={
            200: OpenApiResponse(description="Success message starts with: Sale created successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Invalid sale amounts\n"
                    "- Invalid sale date\n"
                    "- Invalid quantity\n"
                    "- Invalid price\n"
                    "- No variants provided\n"
                    "- Missing Product variant\n"
                    "- The selected product is no longer available and cannot be used for a sale.\n"
                    "- Staff not found.\n"
                    "- Client ID must be a valid integer.\n"
                    "- Paid amount cannot be negative.\n"
                    "- Paid amount cannot exceed the amount due after discount.\n"
                    "- Discount amount cannot be negative.\n"
                    "- Discount cannot exceed total sale amount.\n"
                    "- Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.\n"
                    "- Invalid account selected.\n"
                    "- Invalid or inactive account for this shop."
                )
            ),
            403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
            404: OpenApiResponse(description="Exact message: Product not found in your inventory"),
            500: OpenApiResponse(description="Exact message: Unable to create sale. Please try again."),
        },
        examples=[
            OpenApiExample("Request example", value={"client_phone": "9876543210", "client_phone_e164": "+919876543210", "staff_id": 3, "sale_date": "2026-05-04", "discount": 100, "paid_amount": 1200, "account_id": 2, "decrease_stock": True, "variants": [{"id": 11, "quantity": 1, "amount": 1500}, {"id": 15, "quantity": 1, "amount": 1000}]}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sale created successfully. Invoice queued for sending.", "data": {"sale_id": 55, "total_amount": 2500, "discount": 100, "final_amount": 2400, "paid_amount": 1200, "balance_due": 1200}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Account validation response", value={"status": "validation_error", "message": "Invalid account selected.", "data": {"validation_errors": {"account_id": "Invalid or inactive account for this shop."}}}, response_only=True, status_codes=["400"]),
            OpenApiExample("Product missing response", value={"status": "not_found", "message": "Product not found in your inventory", "data": None}, response_only=True, status_codes=["404"]),
        ],
    )
    @transaction.atomic
    def create(self, request):
        """Create a new sale"""
        try:
            shop_id = self.shop_id
            data = request.data

            # Handle client phone (optional)
            client_phone = None
            raw_phone = data.get("client_phone")
            if raw_phone:
                client_phone, phone_error = self._validate_client_phone(raw_phone)
                if phone_error:
                    return self.validation_error_response(
                        errors={"client_phone": [phone_error]},
                        message=phone_error
                    )

            # Handle client_phone_e164
            client_phone_e164 = None
            raw_phone_e164 = data.get("client_phone_e164")

            if raw_phone_e164:
                from ERP.utils.utils import validate_and_format_phone
                formatted_phone, phone_error = validate_and_format_phone(
                    raw_phone_e164,
                    field_name="client phone"
                )
                if phone_error:
                    return self.validation_error_response(
                        errors={"client_phone_e164": [phone_error]},
                        message=phone_error
                    )
                client_phone_e164 = formatted_phone

            # Validate staff if provided
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

            # Step 1: Validate ALL variants first
            variants = data.get("variants", [])
            if not variants:
                return self.validation_error_response(
                    errors={"variants": ["At least one product variant is required."]},
                    message="No variants provided"
                )

            # Reject soft-deleted variants / variants of soft-deleted products
            if isinstance(variants, list):
                variant_error = self._validate_variant_products(
                    [v.get("id") if isinstance(v, dict) else None for v in variants]
                )
                if variant_error:
                    return variant_error

            # Tax: resolve the shop's active tax configuration once, if any.
            # Sale has no additional_charges/security_amount concept, so only
            # `product_taxable` (which also covers 'entire_invoice', per
            # resolve_taxable_flags) is ever relevant here.
            try:
                shop = Shop.objects.get(id=shop_id)
            except Shop.DoesNotExist:
                return self.validation_error_response(
                    errors={"shop": ["Shop not found."]},
                    message="Shop not found."
                )
            tax_config = get_active_tax_config(shop)
            product_taxable = False
            if tax_config:
                product_taxable, _, _ = resolve_taxable_flags(tax_config.taxable_components)

            decrease_stock = data.get("decrease_stock", False)
            raw_items = []

            for v in variants:
                variant_id = v.get("id")
                if not variant_id:
                    return self.validation_error_response(
                        errors={"variants": ["Product variant ID is required."]},
                        message="Missing Product variant"
                    )

                variant = verify_variant_belongs_to_shop(variant_id, shop_id)
                if not variant:
                    return self.product_not_found_response()

                quantity = v.get("quantity", 1)
                if quantity <= 0:
                    return self.validation_error_response(
                        errors={"variants": ["Quantity must be greater than zero."]},
                        message="Invalid quantity"
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
                        message="Invalid price"
                    )

                raw_items.append({
                    "variant": variant,
                    "quantity": quantity,
                    "price": price,
                })

            discount_amount = data.get("discount", 0)
            discount_dec = Decimal(str(discount_amount))

            # Per-item: price/subtotal are always the raw, as-received amount,
            # for both Exclusive and Inclusive tax - discount/tax are only
            # ever applied at the aggregate (Sale) level below, never
            # distributed back onto individual items.
            total_before_discount = 0
            validated_variants = []
            for it in raw_items:
                price = it["price"]
                quantity = it["quantity"]
                subtotal = price * quantity
                total_before_discount += subtotal
                validated_variants.append({
                    "variant": it["variant"],
                    "quantity": quantity,
                    "price": price,
                    "subtotal": subtotal
                })

            # Tax: aggregate the tax fields once all items have been
            # collected. `subtotal` is the undiscounted, raw sum of stored
            # item amounts; discount only affects the taxable base, not
            # `subtotal`/item amounts themselves.
            tax_fields = None
            if tax_config:
                subtotal_dec = Decimal(str(total_before_discount))
                discounted_subtotal = max(subtotal_dec - discount_dec, Decimal("0.00"))

                if tax_config.tax_calculation_type == "inclusive":
                    # Inclusive: GST is extracted from the discounted total
                    # at the aggregate level only - this is the true,
                    # post-discount taxable base and tax, stored as-is in
                    # taxable_amount/tax_amount. Sale items are never
                    # mutated: each one keeps exactly its raw, as-received
                    # price/subtotal, and total_before_discount stays the
                    # raw (undiscounted) subtotal.
                    if product_taxable:
                        taxable_amount, tax_amount = split_inclusive_amount(discounted_subtotal, tax_config.tax_rate)
                    else:
                        taxable_amount, tax_amount = Decimal("0.00"), Decimal("0.00")

                    total_before_discount = int(subtotal_dec.to_integral_value(rounding=ROUND_HALF_UP))
                else:
                    # Exclusive: tax is added on top of the discounted base.
                    taxable_amount = discounted_subtotal if product_taxable else Decimal("0.00")
                    tax_amount = (
                        (taxable_amount * tax_config.tax_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if product_taxable else Decimal("0.00")
                    )
                    total_before_discount = int(
                        (subtotal_dec + tax_amount).to_integral_value(rounding=ROUND_HALF_UP)
                    )

                tax_fields = {
                    "subtotal": subtotal_dec,
                    "taxable_amount": taxable_amount,
                    "tax_amount": tax_amount,
                    "tax_rate": tax_config.tax_rate,
                    "tax_name": tax_config.tax_name,
                    "tax_calculation_type": tax_config.tax_calculation_type,
                    "is_tax_applied": True,
                    "taxable_components": tax_config.taxable_components,
                }

            # Default paid_amount to the final payable amount (total, already
            # tax-inclusive, minus discount) when the caller omits it entirely,
            # or sends it as null - only a genuinely-provided value is used as-is.
            if data.get("paid_amount") is not None:
                paid_amount = data.get("paid_amount")
            else:
                paid_amount = total_before_discount - discount_amount

            is_valid, error_msg = validate_amounts(total_before_discount, paid_amount, discount_amount)
            if not is_valid:
                return self.validation_error_response(
                    errors={"amount": [error_msg]},
                    message="Invalid sale amounts"
                )

            sale_date, date_error = validate_sale_date(data.get("sale_date"))
            if date_error:
                return self.validation_error_response(
                    errors={"sale_date": [date_error]},
                    message="Invalid sale date"
                )

            # Validate account_id (if supplied) BEFORE creating any records -
            # @transaction.atomic only rolls back on a raised exception, not
            # on an early `return`, so this must happen before Sale/SaleItem
            # creation or an invalid account would leave a committed
            # partially-created sale behind.
            account_id = data.get("account_id")
            account = None
            if paid_amount > 0 and account_id:
                from finance.models import Account
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

            # Step 3: Create Sale
            sale = Sale.objects.create(
                shop_id=shop_id,
                client_phone=client_phone,
                client_phone_e164=client_phone_e164,
                address=data.get("client_address"),
                description=data.get("description", ""),
                sale_date=sale_date,
                total_amount=total_before_discount,
                discount=discount_amount,
                paid_amount=paid_amount,
                decrease_stock=decrease_stock,
                staff=staff,
                **(tax_fields or {}),
            )

            # Step 4: Create SaleItems & Update Stock
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

            # Step 5: Create Payment record if needed
            if paid_amount > 0:
                if account_id:
                    # NEW FLOW: account-based (account already validated above)
                    from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries

                    payment_method = 'cash' if account.account_type == 'cash' else 'upi'
                    payment_record = SalesPaymentDetails.objects.create(
                        sale=sale,
                        payment_amount=paid_amount,
                        payment_method=payment_method,
                        account=account,
                    )
                    _create_ledger_entries(
                        payments=[{
                            'amount': paid_amount,
                            'direction': 'in',
                            'account': account,
                            'payment_method': payment_method,
                            'reference_id': payment_record.id,
                            'created_at': self._get_ledger_timestamp(sale),
                        }],
                        shop=sale.shop,
                        transaction_type='sale_payment',
                        reference_type='SalesPaymentDetails',
                    )
                else:
                    # OLD FLOW: payment_method-based (unchanged)
                    payment_method = data.get("payment_method", "cash")
                    SalesPaymentDetails.objects.create(
                        sale=sale,
                        payment_amount=paid_amount,
                        payment_method=payment_method,
                    )

            # Step 6: Handle WhatsApp invoice sending
            success_message = self._handle_send_invoice(
                sale, data, request.user, "Sale created successfully.",
                client_phone_e164=client_phone_e164
            )

            return self.success_response(
                data={
                    "sale_id": sale.id,
                    "total_amount": sale.total_amount,
                    "discount": sale.discount,
                    "final_amount": sale.total_amount - sale.discount,
                    "paid_amount": sale.paid_amount,
                    "balance_due": sale.total_amount - sale.discount - sale.paid_amount
                },
                message=success_message
            )

        except Exception as e:
            logger.error(f"Error creating sale for shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to create sale. Please try again.")
