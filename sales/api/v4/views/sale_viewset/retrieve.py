import logging

from django.db.models import Prefetch
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from sales.models import Sale
from services.models import ServiceAttributeValue
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation, skip_feature_validation

logger = logging.getLogger('api_requests')


class SaleRetrieveMixin:

    # Allow viewing historical sales even if the shop's subscription has expired.    
    @skip_subscription_validation
    @skip_feature_validation
    @extend_schema(
        tags=["Sales Management V4"],
        summary="Retrieve sale",
        description=(
            "Returns detailed sale data including item attributes, payment account details, and "
            "tax details. The `tax` object reflects the tax snapshot recorded on the sale at "
            "creation/last update (immutable - unaffected by later changes to the shop's tax "
            "configuration); `is_tax_applied=false` means no tax was applied. `total_amount` "
            "already includes `tax_amount` when tax is applied. `total_amount_after_discount` is "
            "`total_amount - discount` (the post-discount payable amount). `items_subtotal` is "
            "the sum of all sale items' amounts only, before tax, additional charges, or discount."
        ),
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Sale retrieved successfully."),
            404: OpenApiResponse(description="Exact message: Sale not found."),
            403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Unable to retrieve sale details."),
        },
        examples=[
            OpenApiExample("Request example", description="GET /api/v4/sales/sales/10/", value={"pk": 10}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sale retrieved successfully.", "data": {"id": 10, "client_phone": "9876543210", "client_phone_e164": "+919876543210", "staff_id": 3, "staff_name": "Ravi", "address": "Andheri", "shop_sale_id": "SAL-10", "description": "Festival order", "sale_date": "04-05-2026", "total_amount": "2950.00", "discount": "100.00", "total_amount_after_discount": "2850.00", "paid_amount": "1000.00", "balance_due": "1850.00", "created_at": "04-05-2026 11:20:10", "items": [], "items_subtotal": "2500.00", "payments": []}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Not found response", value={"status": "not_found", "message": "Sale not found.", "data": None}, response_only=True, status_codes=["404"]),
            OpenApiExample("Server error response", value={"status": "error", "message": "Unable to retrieve sale details.", "data": None}, response_only=True, status_codes=["500"]),
        ],
    )
    def retrieve(self, request, pk=None):
        """Retrieve a specific sale"""
        try:
            shop_id = self.shop_id
            sale = (
                Sale.objects.filter(shop_id=shop_id, id=pk)
                .select_related("staff")
                .prefetch_related(
                    "items__product_variant__product__shop_service__general_service__main_category",
                    "items__product_variant",
                    Prefetch(
                        "items__product_variant__product__attribute_values",
                        queryset=ServiceAttributeValue.objects.select_related("attribute")
                    ),
                    Prefetch(
                        "items__product_variant__attribute_values",
                        queryset=ServiceAttributeValue.objects.select_related("attribute")
                    ),
                    "payments__account"
                )
                .first()
            )

            if not sale:
                return self.not_found_response(message="Sale not found.")

            created_at_local = localtime(sale.created_at).strftime("%d-%m-%Y %H:%M:%S")
            client_phone = (
                sale.client_phone
                or getattr(sale.client, "client_phone_1", None)
            )

            sale_data = {
                "id": sale.id,
                "client_phone": client_phone,
                "client_phone_e164": str(sale.client_phone_e164) if sale.client_phone_e164 else None,
                "staff_id": sale.staff.id if sale.staff else None,
                "staff_name": sale.staff.name if sale.staff else None,
                "address": sale.address,
                "shop_sale_id": sale.shop_sale_id,
                "description": sale.description,
                "sale_date": sale.sale_date,
                "total_amount": sale.total_amount,
                "discount": sale.discount,
                "total_amount_after_discount": sale.total_amount - sale.discount,
                "paid_amount": sale.paid_amount,
                "balance_due": sale.total_amount - sale.discount - sale.paid_amount,
                "created_at": created_at_local,
                "items": [],
                "payments": [],
                "tax": {
                    "is_tax_applied": bool(sale.is_tax_applied),
                    "tax_name": sale.tax_name or None,
                    "tax_rate": float(sale.tax_rate or 0),
                    "tax_calculation_type": sale.tax_calculation_type or None,
                    "taxable_components": sale.taxable_components or [],
                    "subtotal": float(sale.subtotal or 0),
                    "taxable_amount": float(sale.taxable_amount or 0),
                    "tax_amount": float(sale.tax_amount or 0),
                },
            }

            # Sale items
            items_subtotal = 0
            for item in sale.items.all():
                items_subtotal += item.subtotal
                variant = item.product_variant
                product = variant.product

                attributes = {}

                # Product attributes
                for attr in product.attribute_values.all():
                    key = getattr(attr.attribute, "attribute_key", None)
                    if key:
                        attributes[key] = attr.value

                # Variant attributes override product attributes
                for attr in variant.attribute_values.all():
                    key = getattr(attr.attribute, "attribute_key", None)
                    if key:
                        attributes[key] = attr.value

                main_category = (
                    product.shop_service.general_service.main_category
                    if product.shop_service and product.shop_service.general_service
                    else None
                )

                sale_data["items"].append({
                    "id": item.id,
                    "product_id": product.id,
                    "variant_id": variant.id,
                    "product_name": product.name,
                    "variant": variant.attribute,
                    "quantity": item.quantity,
                    "price": item.price,
                    "subtotal": item.subtotal,
                    "thumbnail": product.thumbnail_image,
                    "image": product.image,
                    "color": product.color,
                    "category": product.category,
                    "model": product.model,
                    "main_category_name": main_category.name if main_category else None,
                    "attributes": attributes,
                })

            sale_data["items_subtotal"] = items_subtotal

            # Payment details
            for payment in sale.payments.all():
                sale_data["payments"].append({
                    "id": payment.id,
                    "amount": payment.payment_amount,
                    "method": payment.payment_method,
                    "date": payment.created_at,
                    "account_id": payment.account_id,
                    "account_name": payment.account.name if payment.account else None,
                })

            return self.success_response(
                data=sale_data,
                message="Sale retrieved successfully."
            )
        except Exception as e:
            logger.exception(f"Error retrieving sale details: {e}")
            return self.error_response("Unable to retrieve sale details.")
