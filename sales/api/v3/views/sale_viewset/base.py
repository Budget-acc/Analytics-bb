import logging

from django.db import transaction
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from sales.models import SaleItem, SalesPaymentDetails
from services.models import ProductVariant
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory

from ...utils.validators import validate_sale_date
from ...utils.stock import calculate_available_stock

logger = logging.getLogger('api_requests')


class SaleViewSetBase(StandardResponseMixin, viewsets.ViewSet):
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
            logger.error(f"Shop permission denied in ExpensesViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })

    def _update_variants_logic(self, sale, old_items, new_variants, payment_method=None, new_discount=None):
        """Internal: update sale items (variants) with stock validation"""
        if not new_variants:
            return self.validation_error_response(message="No variants provided")

        shop_id = self.shop_id
        decrease_stock = sale.decrease_stock

        new_variants_map = {v["id"]: v for v in new_variants}
        stock_changes = {}
        variant_objects = {}

        # Step 1: Validate stock & lock variants
        for variant_id, new_v in new_variants_map.items():
            quantity = new_v.get("quantity", 1)
            price = new_v.get("amount", None)

            if quantity <= 0:
                return self.validation_error_response(
                    message=f"Quantity must be greater than zero"
                )

            # Lock ProductVariant
            variant = ProductVariant.objects.select_for_update().filter(
                id=variant_id,
                product__shop_service__shop_id=shop_id,
                is_active=True
            ).first()
            if not variant:
                return self.product_not_found_response()
            variant_objects[variant_id] = variant

            old_item = old_items.get(variant_id)
            diff = quantity - (old_item.quantity if old_item else 0)

            if decrease_stock and diff > 0:
                available = calculate_available_stock(variant)
                if diff > available:
                    return self.insufficient_stock_response(
                        product_name=f"{variant.product.name} - {variant.attribute}",
                        available=available,
                        requested=diff,
                        message="Product have upcoming bookings, cannot reduce stock"
                    )
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
                    old_item.price = price
                old_item.subtotal = old_item.quantity * old_item.price
                old_item.save()
            else:
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

        # Step 3: Recalculate totals
        total = sum(item.quantity * item.price for item in sale.items.all())

        discount_amount = new_discount if new_discount is not None else sale.discount

        final_amount = total - discount_amount

        if discount_amount > total:
            return self.validation_error_response(
                errors={"discount": ["Discount cannot exceed total sale amount."]},
                message="Discount cannot exceed total amount."
            )

        if final_amount < 0:
            return self.validation_error_response(
                errors={"discount": ["Final amount cannot be negative after discount"]},
                message="Final amount cannot be negative after discount"
            )

        sale.total_amount = total
        # sale.paid_amount = min(sale.paid_amount, total)
        sale.paid_amount = final_amount
        sale.discount = discount_amount
        sale.save(update_fields=["total_amount", "discount", "paid_amount"])

        if sale.paid_amount > 0:
            payment_method = payment_method or "cash"
            payment, created = SalesPaymentDetails.objects.update_or_create(
                sale=sale,
                defaults={
                    "payment_amount": sale.paid_amount,
                    "payment_method": payment_method
                }
            )
        else:
            # If paid amount is 0, delete existing payments
            sale.payments.all().delete()

        # return {"data": {"variants_updated": True, "total_amount": total}}
        return self.success_response(
            data={
                "variants_updated": True,
                "total_amount": total,
                "final_amount": final_amount,
                "paid_amount": sale.paid_amount,
                "discount": sale.discount,
            }
        )

    def _update_basic_info_logic(self, sale, data):
        """Internal: update basic sale fields like description, date, discount"""
        if "description" in data:
            sale.description = data["description"]

        if "client_address" in data:
            sale.address = data["client_address"]

        if "sale_date" in data:
            sale_date, error_msg = validate_sale_date(data["sale_date"])
            if error_msg:
                return self.validation_error_response(
                    errors={"sale_date": [error_msg]},
                    message=error_msg
                )
            sale.sale_date = sale_date

        # if "discount" in data:
        #     discount_amount = data["discount"]
        #     is_valid, error_msg = validate_amounts(
        #         sale.total_amount, sale.paid_amount, discount_amount
        #     )
        #     if not is_valid:
        #         return self.validation_error_response(
        #             errors={"discount": [error_msg]},
        #             message=error_msg
        #         )
        #     sale.discount = discount_amount

        if "payment_method" in data:
            payment_method = data["payment_method"]
            payment, created = SalesPaymentDetails.objects.update_or_create(
                sale=sale,
                defaults={
                    "payment_method": payment_method
                }
            )

        sale.save()
        return self.success_response(
            data={"basic_info_updated": True},
            message="Basic info updated successfully."
        )
