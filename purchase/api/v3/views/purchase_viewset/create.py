import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from purchase.models import Purchase, AdditionalCharge
from ERP.utils.shop_activity_logs import create_activity_log
from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries

from .helpers.products import MAX_PRODUCTS_PER_PURCHASE

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Create purchase",
    description=(
        "Records a purchase from a vendor. Two mutually exclusive modes:\n\n"
        "- **With products**: send `existing_products`/`new_products` arrays "
        "(combined max 20 entries - a multi-variant entry still counts as "
        "1). Existing-product entries can atomically increment "
        "ProductVariant.stock and/or update Product.purchase_price. Each "
        "existing_products entry is either flat (quantity + optional "
        "variant_id, for a single variant) or multi-variant "
        "(variants: [{variant_id, quantity}, ...], for purchasing several "
        "variants of the same product at one shared unit_purchase_price/"
        "increment_stock/update_product_cost). New-product entries create "
        "the Product + its variant(s) inline (same variants[] shape). "
        "purchase_total is always taken directly from the request body "
        "(validated as a non-negative number) and saved as-is, in both "
        "modes - it is never computed/derived from item totals. "
        "total_quantity is still computed from the items in this mode.\n"
        "- **Without products**: omit both product arrays and send "
        "purchase_total, total_quantity, tax_rate, tax_amount directly - no "
        "account is required and no ledger entry is created unless an "
        "advance payment is also provided (see paid_amount below).\n\n"
        "Vendor is resolved via vendor_id (reuse existing, no field updates) "
        "or a vendor{} object (matched by name - if a vendor with that name "
        "already exists for this shop, it is reused and any differing "
        "provided fields are updated on it; otherwise a new vendor is "
        "created). purchase_date is optional - if omitted, the current "
        "server date/time is used; if a date-only value is given, the "
        "current time is combined with it. Additional charges are always "
        "immediately settled via their own outward ledger entries, "
        "regardless of mode - they are on-site incidental expenses (e.g. "
        "transport), not part of the vendor's payable balance, and are "
        "excluded from paid_amount/outstanding_balance/advance-payment "
        "validation (which are always against purchase_total, never "
        "grand_total). An optional advance payment to the vendor can be "
        "recorded in the same request via paid_amount - a list of "
        "{account_id, amount} entries, letting a single advance be split "
        "across multiple accounts; the summed amount must not exceed "
        "purchase_total and becomes Purchase.paid_amount. Each selected "
        "account must have sufficient available balance as of purchase_date "
        "(not the account's current/live balance) to cover its share of the "
        "advance payment - same rule as add-payment. If paid_amount is "
        "omitted or empty (either mode), no payment/ledger entry is created "
        "at all and the purchase starts fully unpaid. Supports JSON and "
        "multipart requests (multipart: vendor/existing_products/"
        "new_products/additional_charges/paid_amount as JSON-encoded "
        "strings, documents as files, max 3)."
    ),
    request=inline_serializer(
        name="PurchaseCreateV3Request",
        fields={
            "vendor_id": serializers.IntegerField(required=False),
            "vendor": serializers.DictField(required=False),
            "purchase_date": serializers.CharField(
                required=False,
                help_text="Optional - defaults to the current date/time if omitted. DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS.",
            ),
            "invoice_number": serializers.CharField(required=False),
            "notes": serializers.CharField(required=False),
            "existing_products": serializers.ListField(child=serializers.DictField(), required=False),
            "new_products": serializers.ListField(child=serializers.DictField(), required=False),
            "purchase_total": serializers.DecimalField(max_digits=12, decimal_places=2, required=False),
            "total_quantity": serializers.IntegerField(required=False),
            "tax_rate": serializers.DecimalField(max_digits=5, decimal_places=2, required=False),
            "tax_amount": serializers.DecimalField(max_digits=12, decimal_places=2, required=False),
            "paid_amount": serializers.ListField(
                child=serializers.DictField(), required=False,
                help_text=(
                    "Optional list of advance-payment entries, e.g. "
                    "[{\"account_id\": 1, \"amount\": 1000}, {\"account_id\": 2, \"amount\": 2000}]. "
                    "Each entry may include an optional payment_method override "
                    "(defaults to the value derived from that entry's account_type). "
                    "The sum of all amounts becomes Purchase.paid_amount and must "
                    "not exceed purchase_total (never grand_total). If omitted or "
                    "empty, no payment/ledger entry is created and the purchase "
                    "starts fully unpaid, regardless of mode."
                ),
            ),
            "additional_charges": serializers.ListField(child=serializers.DictField(), required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Purchase created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Invalid purchase date format.\n"
                "- Purchase date cannot be a future date.\n"
                "- Vendor is required.\n"
                "- Vendor not found.\n"
                "- A maximum of 20 products are allowed per purchase.\n"
                "- Product not found.\n"
                "- Invalid quantity.\n"
                "- Invalid purchase price.\n"
                "- Product has no variants.\n"
                "- variant_id is required for this product.\n"
                "- Variant not found for this product.\n"
                "- variant_id is required for each variant entry.\n"
                "- Invalid variant quantity.\n"
                "- Variant {id} not found for this product.\n"
                "- Product name is required.\n"
                "- Shop service is required for new products.\n"
                "- Shop service not found.\n"
                "- Invalid account selected.\n"
                "- account_id is required for each advance payment entry.\n"
                "- Invalid advance payment amount.\n"
                "- Total advance payment cannot exceed purchase_total.\n"
                "- Insufficient balance in this account as of <purchase_date>. Available balance: ₹<available>.\n"
                "- Charge name is required.\n"
                "- Invalid charge amount.\n"
                "- Invalid purchase total.\n"
                "- Invalid total quantity.\n"
                "- Invalid tax rate.\n"
                "- Invalid tax amount.\n"
                "- Cannot exceed 3 attachments per purchase.\n"
                "- Invalid document file type.\n"
                "- Invalid document files."
            )
        ),
        500: OpenApiResponse(
            description=(
                "Exact messages include:\n"
                "- Something went wrong while creating the purchase. (generic/unexpected failure)\n"
                "- Failed to create vendor.\n"
                "- Failed to create new product.\n"
                "- Failed to upload document files. Please try again."
            )
        ),
    },
    examples=[
        OpenApiExample(
            "With-products request",
            value={
                "vendor_id": 1,
                "purchase_date": "10-08-2026",
                "invoice_number": "INV-1001",
                "existing_products": [
                    {"product_id": 5, "quantity": 6, "unit_purchase_price": 450, "increment_stock": True, "update_product_cost": True},
                    {
                        "product_id": 9,
                        "unit_purchase_price": 300,
                        "increment_stock": True,
                        "update_product_cost": True,
                        "variants": [
                            {"variant_id": 21, "quantity": 6},
                            {"variant_id": 22, "quantity": 10},
                        ],
                    },
                ],
                "new_products": [
                    {"product_name": "Silk Saree - Green", "shop_service_id": 2, "unit_purchase_price": 600, "variants": [{"variant_name": "Free Size", "quantity": 5}]}
                ],
                "paid_amount": [{"account_id": 1, "amount": 1000}, {"account_id": 2, "amount": 2000}],
                "additional_charges": [{"charge_name": "Transport", "amount": 200, "account_id": 3}],
            },
            request_only=True,
        ),
    ],
)
@transaction.atomic
def create(self, request):
    savepoint = transaction.savepoint()
    try:
        content_type = request.content_type or ""
        if "multipart/form-data" in content_type:
            request_data = self._parse_multipart_to_json(request.data)
        else:
            request_data = dict(request.data)

        from shop.models import Shop
        shop = Shop.objects.get(id=self.shop_id)

        purchase_date, error = self._parse_purchase_date(request_data)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        vendor, error = self._get_or_create_vendor(request_data, shop, user=request.user)
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        document_files = self._collect_purchase_document_files(request) if request.FILES else []

        existing_products = request_data.get("existing_products") or []
        new_products = request_data.get("new_products") or []
        has_products = bool(existing_products or new_products)

        if has_products and len(existing_products) + len(new_products) > MAX_PRODUCTS_PER_PURCHASE:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"products": [f"A maximum of {MAX_PRODUCTS_PER_PURCHASE} products are allowed per purchase."]},
                message=f"A maximum of {MAX_PRODUCTS_PER_PURCHASE} products are allowed per purchase.",
            )

        without_products_data = None
        purchase_total_value = None
        if not has_products:
            without_products_data, error = self._validate_without_products_fields(request_data)
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
        else:
            # purchase_total is always taken from the request body now, never
            # derived from item totals - same non-negative validation as the
            # without-products path above.
            purchase_total_value, error = self._to_decimal(request_data.get("purchase_total"), "purchase_total")
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

        # Create the Purchase header first (items/charges below reference it).
        try:
            # Nested atomic() so a purchase_code collision here (e.g. a race
            # against a concurrent create for the same shop) is recoverable
            # without poisoning the outer transaction - a bare try/except
            # around a raw IntegrityError here would leave the connection's
            # needs_rollback flag stuck even after the caller's
            # savepoint_rollback below, breaking every later query in the
            # request (TransactionManagementError) - same fix as
            # helpers/vendor.py's _get_or_create_vendor and
            # helpers/products.py's product creation.
            with transaction.atomic():
                purchase = Purchase.objects.create(
                    shop=shop,
                    vendor=vendor,
                    user=request.user,
                    purchase_type="with_products" if has_products else "without_products",
                    invoice_number=request_data.get("invoice_number") or None,
                    purchase_date=purchase_date,
                    notes=request_data.get("notes") or None,
                    tax_rate=without_products_data["tax_rate"] if without_products_data else Decimal("0"),
                    tax_amount=without_products_data["tax_amount"] if without_products_data else Decimal("0"),
                    purchase_total=without_products_data["purchase_total"] if without_products_data else purchase_total_value,
                    total_quantity=without_products_data["total_quantity"] if without_products_data else 0,
                )
        except IntegrityError as e:
            # purchase_code collision against unique_purchase_code_per_shop
            # (Purchase.save()'s counter) - distinct from the generic 500
            # below so the client/support can tell "safe to retry" apart
            # from a genuine failure.
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Purchase create code collision: {str(e)}", exc_info=True)
            return self.validation_error_response(
                errors={"purchase_code": ["Failed to generate a unique purchase code. Please try again."]},
                message="Failed to generate a unique purchase code. Please try again.",
            )

        if has_products:
            total_quantity = 0
            for item_data in existing_products:
                purchase_items, error = self._resolve_existing_product_item(purchase, shop, item_data)
                if error:
                    transaction.savepoint_rollback(savepoint)
                    return error
                for purchase_item in purchase_items:
                    total_quantity += purchase_item.quantity

            for item_data in new_products:
                purchase_items, error = self._create_new_product_item(purchase, shop, item_data)
                if error:
                    transaction.savepoint_rollback(savepoint)
                    return error
                for purchase_item in purchase_items:
                    total_quantity += purchase_item.quantity

            # purchase_total is NOT recomputed here - it was already set from
            # the request body above and must not be derived from item totals.
            purchase.total_quantity = total_quantity
            purchase.save(update_fields=["total_quantity", "grand_total"])

        # Optional split advance payment against purchase_total - applies to
        # both modes uniformly. No-op (no account, no ledger entry) if
        # paid_amount is omitted/empty, since a purchase is only ever
        # settled when money actually changes hands.
        _, error = self._process_advance_payments(purchase, shop, request_data.get("paid_amount") or [])
        if error:
            transaction.savepoint_rollback(savepoint)
            return error

        # Additional charges - always immediately settled, both modes.
        additional_charges_data = request_data.get("additional_charges") or []
        charge_ledger_payments = []
        for charge_data in additional_charges_data:
            charge_name = (charge_data.get("charge_name") or "").strip()
            if not charge_name:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"additional_charges": ["charge_name is required."]},
                    message="Charge name is required.",
                )
            amount, error = self._to_decimal(charge_data.get("amount"), "amount")
            if error or amount <= 0:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"additional_charges": ["amount must be a positive number."]},
                    message="Invalid charge amount.",
                )
            account, payment_method, error = self._resolve_account(
                shop, charge_data.get("account_id"), field_name="additional_charges.account_id"
            )
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

            charge = AdditionalCharge.objects.create(
                purchase=purchase,
                charge_name=charge_name,
                amount=amount,
                account=account,
                payment_method=payment_method,
            )
            charge_ledger_payments.append({
                "amount": amount,
                "direction": "out",
                "account": account,
                "payment_method": payment_method,
                "reference_id": charge.id,
                "created_at": purchase.purchase_date,
            })

        if charge_ledger_payments:
            _create_ledger_entries(
                payments=charge_ledger_payments,
                shop=shop,
                transaction_type="purchase_additional_charge",
                reference_type="AdditionalCharge",
            )

        if additional_charges_data:
            # AdditionalCharge.save() updates additional_charges_total (and
            # therefore grand_total) via a raw queryset .update() that
            # bypasses Purchase.save() and never touches this in-memory
            # purchase object - refresh so the response/any later save
            # reflects the charges just created, not a stale pre-charges
            # value.
            purchase.refresh_from_db(fields=["additional_charges_total", "grand_total"])

        # Documents - synchronous, cap is small (3), no async split needed.
        if document_files:
            urls, error = self._upload_purchase_documents(document_files, shop, request.user, savepoint)
            if error:
                return error
            # Count already enforced by _upload_purchase_documents before
            # upload started - no need to re-validate via Purchase.clean().
            purchase.documents = urls
            purchase.save(update_fields=["documents"])

        transaction.savepoint_commit(savepoint)

        try:
            create_activity_log(
                shop=shop,
                user=request.user,
                instance=purchase,
                action="created",
                message=f"Purchase {purchase.purchase_code} created from {vendor.name}.",
            )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for purchase creation: {log_error}")

        return self.success_response(
            data={
                "purchase_id": purchase.id,
                "purchase_code": purchase.purchase_code,
                "grand_total": purchase.grand_total,
                "paid_amount": purchase.paid_amount,
                "outstanding_balance": purchase.outstanding_balance,
            },
            message="Purchase created successfully.",
        )

    except Exception as e:
        # Log first - if the rollback below itself fails (the connection's
        # transaction can already be broken beyond savepoint scope, e.g.
        # after an IntegrityError), the original exception must still make
        # it into the logs, not be replaced by a secondary one.
        logger.error(f"Purchase create error: {str(e)}", exc_info=True)
        try:
            transaction.savepoint_rollback(savepoint)
        except Exception as rollback_error:
            # A broken savepoint rollback is expected in this situation -
            # the outer @transaction.atomic will still roll back the whole
            # transaction once this exception handler returns/raises, so
            # swallowing it here is safe. Letting it propagate instead would
            # replace the real error with this one and escape all the way
            # to an unhandled Django 500 with no JSON body.
            logger.error(
                f"Purchase create savepoint rollback also failed: {rollback_error}",
                exc_info=True,
            )
        return self.error_response(
            message="Something went wrong while creating the purchase.",
            dev_message=str(e),
        )


__all__ = ["create"]
