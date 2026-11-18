import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils.timezone import make_aware
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from sales.models import SaleItem, SalesPaymentDetails
from services.models import ProductVariant
from shop.api.v3.utils.shop import get_active_shop_id
from shop.api.v3.utils.tax_calculation import resolve_taxable_flags, split_inclusive_amount
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory

from ...utils.validators import validate_client_phone, validate_sale_date, get_soft_deleted_variant_ids
from ...utils.stock import calculate_available_stock

logger = logging.getLogger('api_requests')

PRODUCT_UNAVAILABLE_MESSAGE = "The selected product is no longer available and cannot be used for a sale."


class SaleViewSetBase(StandardResponseMixin, viewsets.ViewSet):
    # send_invoice is exempt so invoices can still be sent during an expired/inactive subscription grace period.
    # subscription_exempt_actions = {"send_invoice"}
    subscription_exempt_methods = {"OPTIONS", "HEAD"}
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        FeatureRolePermissionFactory("SALES", allowed_roles=["OWNER", "MANAGER", "STAFF"]),
    ]
    authentication_classes = [CutoffJWTAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in SaleViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })

    def _validate_client_phone(self, phone):
        return validate_client_phone(phone)

    def _validate_sale_date(self, sale_date):
        return validate_sale_date(sale_date)

    def _validate_variant_products(self, variant_ids):
        """
        Reject variants that are soft-deleted or whose product is soft-deleted.
        Same user-facing message for both; dev_message says which one.
        Returns error_response or None.
        """
        deleted_variant_ids, deleted_product_variant_ids = get_soft_deleted_variant_ids(
            variant_ids, self.shop_id
        )
        if not deleted_variant_ids and not deleted_product_variant_ids:
            return None

        dev_parts = []
        if deleted_variant_ids:
            dev_parts.append(f"ProductVariant IDs {deleted_variant_ids} are soft-deleted.")
        if deleted_product_variant_ids:
            dev_parts.append(f"ProductVariant IDs {deleted_product_variant_ids} belong to a soft-deleted Product.")

        return self.validation_error_response(
            errors={
                "variants": [PRODUCT_UNAVAILABLE_MESSAGE],
                "deleted_variant_ids": sorted(deleted_variant_ids + deleted_product_variant_ids),
            },
            message=PRODUCT_UNAVAILABLE_MESSAGE,
            dev_message=" ".join(dev_parts),
        )

    def _calculate_available_stock(self, variant):
        return calculate_available_stock(variant)

    def _update_variants_logic(self, sale, old_items, new_variants, payment_method=None, new_discount=None, account_id=None, new_paid_amount=None):
        """Internal: update sale items (variants) with stock validation"""
        if not new_variants:
            return {"status": "error", "message": "No variants provided"}

        shop_id = self.shop_id
        decrease_stock = sale.decrease_stock

        # Validate account_id (if explicitly supplied) BEFORE any stock/item/
        # sale mutations below - the caller's @transaction.atomic only rolls
        # back on a raised exception, not on an early `return`, so an invalid
        # account must be rejected before anything is written or the
        # already-applied changes would be committed despite the error.
        # (The later fallback-to-existing-payment-account path is untouched
        # since that account_id was already validated when first stored.)
        account = None
        if account_id:
            from finance.models import Account
            try:
                account = Account.objects.get(
                    id=account_id,
                    shop_id=shop_id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except Account.DoesNotExist:
                return {"status": "error", "message": "Invalid account selected."}

        new_variants_map = {v["id"]: v for v in new_variants}
        stock_changes = {}
        variant_objects = {}

        # Step 1: Validate stock & lock variants
        for variant_id, new_v in new_variants_map.items():
            quantity = new_v.get("quantity", 1)
            price = new_v.get("amount", None)

            if quantity <= 0:
                return {"status": "error", "message": "Quantity must be greater than zero."}

            variant = ProductVariant.objects.select_for_update().filter(
                id=variant_id,
                product__shop_service__shop_id=shop_id,
                is_active=True
            ).first()
            if not variant:
                return {"status": "error", "message": "Product not found."}

            variant_objects[variant_id] = variant
            old_item = old_items.get(variant_id)
            diff = quantity - (old_item.quantity if old_item else 0)

            if decrease_stock and diff > 0:
                available = calculate_available_stock(variant)
                if diff > available:
                    return {
                        "status": "error",
                        "message": (
                            f"Insufficient stock for {variant.product.name} ({variant.attribute}). "
                            f"Available: {available}, requested: {diff}."
                        )
                    }
            stock_changes[variant_id] = diff

        # Step 2: Apply changes
        for variant_id, old_item in old_items.items():
            if variant_id not in new_variants_map:
                if decrease_stock:
                    old_item.product_variant.stock += old_item.quantity
                    old_item.product_variant.save(update_fields=["stock"])
                old_item.delete()

        for variant_id, new_v in new_variants_map.items():
            quantity = new_v.get("quantity", 1)
            price = new_v.get("amount", None)
            variant = variant_objects[variant_id]
            old_item = old_items.get(variant_id)
            diff = stock_changes[variant_id]

            if old_item:
                if decrease_stock:
                    variant.stock -= diff
                    variant.save(update_fields=["stock"])

                old_item.quantity = quantity
                if price is not None:
                    # price stores the raw, as-received value - never
                    # discount- or tax-adjusted (Step 3 below computes
                    # discount/tax purely at the aggregate level).
                    old_item.price = price
                old_item.subtotal = old_item.quantity * old_item.price
                old_item.save()
            else:
                # New item - no prior state, so a provided price is always
                # treated as fresh (raw) input.
                subtotal = quantity * (price or variant.price)
                SaleItem.objects.create(
                    sale=sale,
                    product_variant=variant,
                    quantity=quantity,
                    price=price or variant.price,
                    subtotal=subtotal
                )
                if decrease_stock:
                    variant.stock -= quantity
                    variant.save(update_fields=["stock"])

        # Step 3: Recalculate totals. Item price/subtotal are NEVER discount-
        # or tax-adjusted - each item already stores exactly its own raw
        # amount (Step 2 above never mutates it for tax either). Discount
        # and tax only affect the aggregate fields below, and discount is
        # applied once, downstream, to derive final_amount/paid_amount.
        sale_items_all = list(sale.items.all())
        discount_amount = new_discount if new_discount is not None else sale.discount
        discount_dec = Decimal(str(discount_amount))

        subtotal_dec = sum(
            (Decimal(str(item.price)) * item.quantity for item in sale_items_all), Decimal("0")
        )

        if getattr(sale, "is_tax_applied", False):
            recalculated_product_taxable, _, _ = resolve_taxable_flags(sale.taxable_components)
            discounted_subtotal = max(subtotal_dec - discount_dec, Decimal("0.00"))

            if sale.tax_calculation_type == "inclusive":
                # Inclusive: GST is extracted from the discounted total at
                # the aggregate level only - this is the true, post-discount
                # taxable base and tax, stored as-is in
                # taxable_amount/tax_amount. Sale items are never mutated:
                # each one keeps exactly its raw, as-received price/subtotal
                # (already set in Step 2 above).
                if recalculated_product_taxable:
                    taxable_amount, tax_amount = split_inclusive_amount(discounted_subtotal, sale.tax_rate)
                else:
                    taxable_amount, tax_amount = Decimal("0.00"), Decimal("0.00")

                total = int(subtotal_dec.to_integral_value(rounding=ROUND_HALF_UP))
            else:
                # Exclusive: tax is added on top of the discounted base.
                taxable_amount = discounted_subtotal if recalculated_product_taxable else Decimal("0.00")
                tax_amount = (
                    (taxable_amount * sale.tax_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if recalculated_product_taxable else Decimal("0.00")
                )
                total = int((subtotal_dec + tax_amount).to_integral_value(rounding=ROUND_HALF_UP))

            sale.subtotal = subtotal_dec
            sale.taxable_amount = taxable_amount
            sale.tax_amount = tax_amount
        else:
            total = int(subtotal_dec.to_integral_value(rounding=ROUND_HALF_UP))

        if discount_amount > total:
            return {
                "status": "error",
                "message": "Discount cannot exceed total sale amount."
            }

        final_amount = total - discount_amount

        if final_amount < 0:
            return {
                "status": "error",
                "message": "Final amount cannot be negative after discount."
            }

        # paid_amount: honor an explicitly-supplied value (e.g. a partial payment),
        # otherwise default to the final payable amount - same default this always
        # applied before new_paid_amount existed, so omitting it is unchanged.
        if new_paid_amount is not None:
            try:
                paid_amount = Decimal(str(new_paid_amount))
            except Exception:
                return {"status": "error", "message": "Invalid paid amount."}
            if paid_amount < 0:
                return {"status": "error", "message": "Paid amount cannot be negative."}
            if paid_amount > final_amount:
                return {"status": "error", "message": "Paid amount cannot exceed the amount due after discount."}
            if isinstance(total, int):
                paid_amount = int(paid_amount)
        else:
            paid_amount = final_amount

        sale.total_amount = total
        sale.paid_amount = paid_amount
        sale.discount = discount_amount
        update_fields = ["total_amount", "discount", "paid_amount"]
        if getattr(sale, "is_tax_applied", False):
            update_fields += ["subtotal", "taxable_amount", "tax_amount"]
        sale.save(update_fields=update_fields)

        if sale.paid_amount > 0:
            # If account_id not provided, fall back to the previously stored account
            if not account_id:
                existing = sale.payments.filter(account__isnull=False).first()
                if existing:
                    account_id = existing.account_id

            if account_id:
                # NEW FLOW: account-based — delete old ledger, create new one.
                # `account` is already validated/fetched above when it was
                # explicitly supplied; only re-fetch here for the fallback
                # case (account_id resolved from an existing payment above).
                from finance.api.v3.utils import (
                    create_ledger_entries as _create_ledger_entries,
                    delete_ledger_entries as _delete_ledger_entries,
                    update_daily_balance as _update_daily_balance,
                )

                if account is None:
                    from finance.models import Account
                    try:
                        account = Account.objects.get(
                            id=account_id,
                            shop_id=self.shop_id,
                            deleted__isnull=True,
                            is_active=True,
                        )
                    except Account.DoesNotExist:
                        return {"status": "error", "message": "Invalid account selected."}

                # Delete old ledger entries and update old daily balance
                old_payments = list(sale.payments.filter(account__isnull=False))
                old_payment_ids = [p.id for p in old_payments]
                old_account_date_pairs = {(p.account_id, sale.sale_date) for p in old_payments}

                sale.payments.all().delete()

                if old_payment_ids:
                    _delete_ledger_entries(reference_ids=old_payment_ids, reference_type='SalesPaymentDetails')
                    for acct_id, date in old_account_date_pairs:
                        _update_daily_balance(shop=sale.shop_id, account=acct_id, date=date)

                # Create new payment record and ledger entry
                pm = 'cash' if account.account_type == 'cash' else 'upi'
                payment_record = SalesPaymentDetails.objects.create(
                    sale=sale,
                    payment_amount=sale.paid_amount,
                    payment_method=pm,
                    account=account,
                )
                _create_ledger_entries(
                    payments=[{
                        'amount': sale.paid_amount,
                        'direction': 'in',
                        'account': account,
                        'payment_method': pm,
                        'reference_id': payment_record.id,
                        'created_at': make_aware(datetime.combine(sale.sale_date, datetime.now().time())),
                    }],
                    shop=sale.shop,
                    transaction_type='sale_payment',
                    reference_type='SalesPaymentDetails',
                )
            else:
                # OLD FLOW: payment_method-based (no ledger)
                payment_method = payment_method or "cash"
                SalesPaymentDetails.objects.update_or_create(
                    sale=sale,
                    defaults={
                        "payment_amount": sale.paid_amount,
                        "payment_method": payment_method,
                    }
                )
        else:
            # paid_amount is 0 — delete all payments and their ledger entries
            old_payments = list(sale.payments.filter(account__isnull=False))
            old_payment_ids = [p.id for p in old_payments]
            old_account_date_pairs = {(p.account_id, sale.sale_date) for p in old_payments}

            sale.payments.all().delete()

            if old_payment_ids:
                from finance.api.v3.utils import (
                    delete_ledger_entries as _delete_ledger_entries,
                    update_daily_balance as _update_daily_balance,
                )
                _delete_ledger_entries(reference_ids=old_payment_ids, reference_type='SalesPaymentDetails')
                for acct_id, date in old_account_date_pairs:
                    _update_daily_balance(shop=sale.shop_id, account=acct_id, date=date)

        return {
            "status": "success",
            "data": {
                "variants_updated": True,
                "total_amount": total,
                "final_amount": final_amount,
                "paid_amount": sale.paid_amount,
                "discount": sale.discount,
            }
        }

    def _update_basic_info_logic(self, sale, data):
        """Internal: update basic sale fields like description, date, discount"""
        if "description" in data:
            sale.description = data["description"]

        if "client_address" in data:
            sale.address = data["client_address"]

        if "client_phone" in data:
            raw_phone = data["client_phone"]
            if raw_phone:
                client_phone, phone_error = self._validate_client_phone(raw_phone)
                if phone_error:
                    return {
                        "status": "error",
                        "message": phone_error,
                        "errors": {"client_phone": [phone_error]}
                    }
                sale.client_phone = client_phone
            else:
                sale.client_phone = None

        if "sale_date" in data:
            sale_date, error_msg = self._validate_sale_date(data["sale_date"])
            if error_msg:
                return self.validation_error_response(
                    errors={"sale_date": [error_msg]},
                    message=error_msg
                )
            sale.sale_date = sale_date

        if "payment_method" in data:
            payment_method = data["payment_method"]
            payment, created = SalesPaymentDetails.objects.update_or_create(
                sale=sale,
                defaults={
                    "payment_method": payment_method
                }
            )

        sale.save()
        return {
            "status": "success",
            "data": {"basic_info_updated": True}
        }

    def _handle_send_invoice(self, sale, data, user, base_message, client_phone_e164=None):
        """
        Check send_invoice flag and queue a WhatsApp invoice task if eligible.
        Returns the final success message reflecting the invoice outcome.
        Requires client_phone_e164 to be present (sale-specific guard).
        """
        from django.core.cache import cache

        send_invoice = data.get("send_invoice", False)
        if send_invoice not in [True, "true", "True", 1, "1"] or not client_phone_e164:
            return base_message

        try:
            from subscriptions.models import has_feature_access
            from shop.models import Shop, ShopUser

            shop_obj = Shop.objects.select_related('subscription').get(id=self.shop_id)

            if not hasattr(shop_obj, 'subscription') or not shop_obj.subscription:
                return f"{base_message} Invoice not sent - Shop does not have an active subscription."
            elif not shop_obj.subscription.is_active():
                return f"{base_message} Invoice not sent - Shop subscription is inactive or expired."

            shop_user = ShopUser.objects.filter(shop_id=self.shop_id, user=user).first()
            if not shop_user:
                return f"{base_message} Invoice not sent - User not associated with this shop."
            elif not has_feature_access(shop_user, "WHATSAPP_MESSAGE"):
                return f"{base_message} Invoice not sent - Feature not available."

            cache_key = f"wa_task_queue_sale_{shop_obj.id}"
            queued_count = cache.get(cache_key, 0)
            if queued_count >= 10:
                logger.warning(f"Rate limit exceeded for shop {shop_obj.id}: {queued_count} tasks queued")
                return f"{base_message} Too many pending invoices, please try again later."

            from whatsapp_integration.v3.tasks import send_sale_invoice_task
            send_sale_invoice_task.delay(sale.id)
            cache.set(cache_key, queued_count + 1, timeout=60)
            logger.info(f"WhatsApp invoice task queued for sale {sale.id} (queue count: {queued_count + 1})")
            return f"{base_message} Invoice queued for sending."

        except Exception as e:
            logger.error(f"WhatsApp invoice validation/queue failed for sale {sale.id}: {str(e)}", exc_info=True)
            return f"{base_message} WhatsApp invoice not sent - {str(e)}"
