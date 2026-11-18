import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Prefetch
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

from purchase.models import Purchase, PurchaseItem

logger = logging.getLogger("api_requests")


def _format_datetime(value):
    """Render a datetime as 'YYYY-MM-DD HH:MM:SS' in local time, or None."""
    if not value:
        return None
    return localtime(value).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_decimal(value, places=2):
    """
    Quantize a Decimal to a fixed number of places so JSON output is
    consistently formatted regardless of the value's original stored
    precision (e.g. Decimal('18') vs Decimal('0.00')) - both render the
    same way once normalized here.
    """
    if value is None:
        return None
    return Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Retrieve purchase",
    description=(
        "Returns a purchase's full header, all items, all additional charges, and all payments. "
        "Each item includes product_image and product_thumbnail_image (both nullable) sourced "
        "from the linked product, for both existing-product and newly-created-product items."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purchase ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Purchase fetched successfully."),
        404: OpenApiResponse(description="Exact message: Purchase not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the purchase."),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        purchase = (
            Purchase.objects.select_related("vendor", "user")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=PurchaseItem.objects.select_related("product_variant__product"),
                ),
                "additional_charges__account",
                "payments__account",
            )
            .filter(id=pk, shop_id=self.shop_id, deleted__isnull=True)
            .first()
        )
        if not purchase:
            return self.not_found_response(
                resource="Purchase",
                message="Purchase not found.",
                dev_message=f"Purchase with id {pk} not found for shop {self.shop_id}.",
            )

        items = [
            {
                "id": item.id,
                "product_id": item.product_variant.product_id if item.product_variant_id else None,
                "product_variant_id": item.product_variant_id,
                "product_name": item.product_name,
                "variant": item.variant_other,
                "quantity": item.quantity,
                "purchase_amount": _normalize_decimal(item.purchase_amount),
                "line_total": _normalize_decimal(item.line_total),
                "stock_increased": item.stock_increased,
                "update_product_cost": item.update_product_cost,
                "line_group": str(item.line_group) if item.line_group else None,
                "item_source": item.item_source,
                "product_image": item.product_variant.product.image if item.product_variant_id else None,
                "product_thumbnail_image": item.product_variant.product.thumbnail_image if item.product_variant_id else None,
            }
            for item in purchase.items.all()
        ]
        additional_charges = [
            {
                "id": c.id,
                "charge_name": c.charge_name,
                "amount": _normalize_decimal(c.amount),
                "account_id": c.account_id,
                "account_name": c.account.name if c.account_id else None,
                "payment_method": c.payment_method,
                "created_at": _format_datetime(c.created_at),
            }
            for c in purchase.additional_charges.all()
        ]
        payments = [
            {
                "id": p.id,
                "amount": _normalize_decimal(p.amount),
                "account_id": p.account_id,
                "account_name": p.account.name if p.account_id else None,
                "payment_method": p.payment_method,
                "notes": p.notes,
                "created_at": _format_datetime(p.created_at),
            }
            for p in purchase.payments.all()
        ]

        data = {
            "id": purchase.id,
            "purchase_code": purchase.purchase_code,
            "purchase_type": purchase.purchase_type,
            "vendor": {
                "id": purchase.vendor_id,
                "name": purchase.vendor.name if purchase.vendor_id else None,
                "contact_number": str(purchase.vendor.contact_number) if purchase.vendor_id and purchase.vendor.contact_number else None,
                "location": purchase.vendor.location if purchase.vendor_id else None,
                "gst_number": purchase.vendor.gst_number if purchase.vendor_id else None,
            },
            "invoice_number": purchase.invoice_number,
            "purchase_date": _format_datetime(purchase.purchase_date),
            "notes": purchase.notes,
            "tax_rate": _normalize_decimal(purchase.tax_rate),
            "tax_amount": _normalize_decimal(purchase.tax_amount),
            "purchase_total": _normalize_decimal(purchase.purchase_total),
            "total_quantity": purchase.total_quantity,
            "additional_charges_total": _normalize_decimal(purchase.additional_charges_total),
            "grand_total": _normalize_decimal(purchase.grand_total),
            "paid_amount": _normalize_decimal(purchase.paid_amount),
            "outstanding_balance": _normalize_decimal(purchase.outstanding_balance),
            "is_fully_paid": purchase.is_fully_paid,
            "is_completed": purchase.is_completed,
            "documents": purchase.documents,
            "completed_at": _format_datetime(purchase.completed_at),
            "created_at": _format_datetime(purchase.created_at),
            "items": items,
            "additional_charges": additional_charges,
            "payments": payments,
        }

        return self.success_response(data=data, message="Purchase fetched successfully.")

    except Exception as e:
        logger.error(f"Purchase retrieve error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the purchase.",
            dev_message=str(e),
        )


__all__ = ["retrieve"]
