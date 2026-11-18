import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from ERP.utils.shop_activity_logs import create_activity_log
from sales.models import Sale, SalesPaymentDetails
from shop.api.v3.utils.tax_calculation import resolve_taxable_flags, split_inclusive_amount

from ...utils.validators import validate_amounts

logger = logging.getLogger('api_requests')


class SalePartialUpdateMixin:

    def _get_ledger_timestamp(self, sale):
        """Use sale record timestamp when available, else fallback to current time."""
        ts = getattr(sale, 'created_at', None) or timezone.now()
        return timezone.make_aware(ts) if timezone.is_naive(ts) else ts

    @extend_schema(
        tags=["Sales Management V4"],
        summary="Partial update sale",
        description="Updates sale core fields including phone, date, discount, and paid amount with optional account-based payment rewrite.",
        parameters=[OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id.")],
        request=inline_serializer(
            name="SalesV4PartialUpdateRequest",
            fields={
                "description": serializers.CharField(required=False, allow_blank=True),
                "client_phone": serializers.CharField(required=False),
                "sale_date": serializers.CharField(required=False),
                "discount": serializers.FloatField(required=False),
                "paid_amount": serializers.FloatField(required=False),
                "payment_method": serializers.CharField(required=False),
                "account_id": serializers.IntegerField(required=False),
                "send_invoice": serializers.BooleanField(required=False),
                "client_phone_e164": serializers.CharField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(description="Success message starts with: Sale updated successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Invalid paid amount\n"
                    "- Paid amount cannot be negative.\n"
                    "- Paid amount cannot exceed the amount due after discount.\n"
                    "- Invalid account selected.\n"
                    "- Invalid or inactive account for this shop.\n"
                    "- Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.\n"
                    "- Discount cannot exceed total sale amount.\n"
                    "- Discount amount cannot be negative."
                )
            ),
            404: OpenApiResponse(description="Exact message: Sale not found."),
            500: OpenApiResponse(description="Exact message: Unable to update sale."),
        },
        examples=[
            OpenApiExample("Request example", value={"description": "Updated note", "discount": 120, "paid_amount": 1300, "account_id": 2, "send_invoice": True}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sale updated successfully. Invoice queued for sending.", "data": {"sale_id": 55}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Validation response", value={"status": "validation_error", "message": "Invalid paid amount", "data": {"validation_errors": {"paid_amount": ["Paid amount cannot exceed the amount due after discount."]}}}, response_only=True, status_codes=["400"]),
            OpenApiExample("Not found response", value={"status": "not_found", "message": "Sale not found.", "data": None}, response_only=True, status_codes=["404"]),
        ],
    )
    @transaction.atomic
    def partial_update(self, request, pk=None):
        """Update sale basic information"""
        try:
            shop_id = self.shop_id
            sale = Sale.objects.filter(shop_id=shop_id, id=pk).first()
            if not sale:
                return self.not_found_response("Sale not found.")

            data = request.data
            changed_fields = []

            if "description" in data:
                sale.description = data["description"]
                changed_fields.append("description")

            if "client_phone" in data:
                raw_phone = data["client_phone"]
                if raw_phone:
                    client_phone, phone_error = self._validate_client_phone(raw_phone)
                    if phone_error:
                        return self.validation_error_response(
                            errors={"client_phone": [phone_error]},
                            message=phone_error
                        )
                    sale.client_phone = client_phone
                else:
                    sale.client_phone = None
                changed_fields.append("client_phone")

            if "sale_date" in data:
                sale_date, error_msg = self._validate_sale_date(data["sale_date"])
                if error_msg:
                    return self.validation_error_response(
                        errors={"sale_date": [error_msg]},
                        message=error_msg
                    )
                sale.sale_date = sale_date
                changed_fields.append("sale_date")

            if "discount" in data:
                discount_amount = data["discount"]
                is_valid, error_msg = validate_amounts(sale.total_amount, sale.paid_amount, discount_amount)
                if not is_valid:
                    return self.validation_error_response(
                        errors={"discount": [error_msg]},
                        message=error_msg
                    )
                sale.discount = discount_amount
                changed_fields.append("discount")

                # Inclusive tax: discount only affects the taxable subtotal
                # extraction - sale items themselves are never touched, and
                # `subtotal` (raw item sum) is unaffected by discount, so it
                # can be reused as-is here. Recompute tax_amount/taxable_amount
                # from the new discount (previously this was never
                # recalculated on a discount-only update, leaving a stale
                # tax snapshot). Exclusive tax is intentionally left as-is.
                if getattr(sale, "is_tax_applied", False) and sale.tax_calculation_type == "inclusive":
                    product_taxable, _, _ = resolve_taxable_flags(sale.taxable_components)
                    discount_dec = Decimal(str(discount_amount))
                    subtotal_dec = Decimal(str(sale.subtotal or 0))
                    discounted_taxable = max(subtotal_dec - discount_dec, Decimal("0.00"))
                    if product_taxable:
                        sale.taxable_amount, sale.tax_amount = split_inclusive_amount(discounted_taxable, sale.tax_rate)
                    else:
                        sale.taxable_amount, sale.tax_amount = Decimal("0.00"), Decimal("0.00")
                    changed_fields.append("tax_amount")

            if "paid_amount" in data:
                new_paid_amount = data["paid_amount"]
                account_id = data.get("account_id")

                if new_paid_amount < 0:
                    return self.validation_error_response(
                        errors={"paid_amount": ["Paid amount cannot be negative."]},
                        message="Invalid paid amount"
                    )
                final_amount = sale.total_amount - sale.discount
                if new_paid_amount > final_amount:
                    return self.validation_error_response(
                        errors={"paid_amount": ["Paid amount cannot exceed the amount due after discount."]},
                        message="Invalid paid amount"
                    )

                # If account_id not in request, fall back to previously stored account
                # (only needed when new_paid_amount > 0)
                if new_paid_amount > 0 and not account_id:
                    existing = sale.payments.filter(account__isnull=False).first()
                    if existing:
                        account_id = existing.account_id

                if account_id:
                    # NEW FLOW: account-based — delete old ledger, create new one
                    from finance.models import Account
                    from finance.api.v3.utils import (
                        create_ledger_entries as _create_ledger_entries,
                        delete_ledger_entries as _delete_ledger_entries,
                        update_daily_balance as _update_daily_balance,
                    )

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

                    # Step 1: Delete old ledger entries and update old daily balance
                    old_payments = list(sale.payments.filter(account__isnull=False))
                    old_payment_ids = [p.id for p in old_payments]
                    old_account_date_pairs = {(p.account_id, sale.sale_date) for p in old_payments}

                    if old_payment_ids:
                        _delete_ledger_entries(reference_ids=old_payment_ids, reference_type='SalesPaymentDetails')
                        for acct_id, date in old_account_date_pairs:
                            _update_daily_balance(shop=shop_id, account=acct_id, date=date)

                    # Step 2: Replace all payment records
                    sale.payments.all().delete()
                    sale.paid_amount = new_paid_amount

                    if new_paid_amount > 0:
                        payment_method = 'cash' if account.account_type == 'cash' else 'upi'
                        payment_record = SalesPaymentDetails.objects.create(
                            sale=sale,
                            payment_amount=new_paid_amount,
                            payment_method=payment_method,
                            account=account,
                        )
                        # Step 3: Create new ledger entry and update new daily balance
                        _create_ledger_entries(
                            payments=[{
                                'amount': new_paid_amount,
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
                    # OLD FLOW: payment_method-based (no ledger)
                    sale.paid_amount = new_paid_amount
                    if new_paid_amount > 0:
                        payment_method_val = data.get("payment_method", "cash")
                        SalesPaymentDetails.objects.update_or_create(
                            sale=sale,
                            defaults={"payment_amount": new_paid_amount, "payment_method": payment_method_val}
                        )
                    else:
                        sale.payments.all().delete()

                changed_fields.append("paid_amount")

            sale.save()

            try:
                create_activity_log(
                    shop=sale.shop,
                    user=request.user,
                    instance=sale,
                    action="updated",
                    message=(
                        f"Fields changed: {', '.join(changed_fields) if changed_fields else 'none'}."
                    )
                )
            except Exception as log_err:
                logger.warning(f"Failed to create activity log for sale partial_update: {log_err}")

            effective_phone = data.get("client_phone_e164") or sale.client_phone_e164
            success_message = self._handle_send_invoice(
                sale, data, request.user, "Sale updated successfully.",
                client_phone_e164=effective_phone
            )
            return self.success_response(data={"sale_id": sale.id}, message=success_message)

        except Exception as e:
            logger.error(f"Error updating sale {pk}: {e}", exc_info=True)
            return self.error_response("Unable to update sale.")
