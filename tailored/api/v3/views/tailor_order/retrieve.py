from tailored.models import TailorOrder, TailorOrderItem, TailorMaterialUsage, CustomerPayment, TailorOrderSecurityPayment, TailorOrderRefund, TailorOrderSecurityDepositAdjustment, TailorOrderItemAssignment
from tailored.api.v3.serializers import TailorOrderDetailSerializer
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Prefetch
from django.utils.timezone import localtime
from decimal import Decimal
from finance.models import Account
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation, skip_feature_validation
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Retrieve tailor order",
    description=(
        "Returns full tailor order details, including items, materials, payments, security, "
        "computed totals, and tax details. The `tax` object reflects the tax snapshot recorded "
        "on the order at creation/last update (immutable - unaffected by later changes to the "
        "shop's tax configuration); `is_tax_applied=false` means no tax was applied. "
        "`total_payable` and `balance` already include `tax_amount` when tax is applied."
    ),
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor order id.")],
    responses={
        200: OpenApiResponse(description="Exact success message: Tailor order details retrieved successfully."),
        400: OpenApiResponse(description="Exact error message: Invalid order ID provided."),
        404: OpenApiResponse(description="Exact error message: Tailor order not found."),
        500: OpenApiResponse(description="Exact error message: Unable to retrieve tailor order details. Please try again later."),
    },
    examples=[OpenApiExample("Retrieve success", value={"status": "success", "message": "Tailor order details retrieved successfully.", "data": {"id": 8, "shop_tailor_id": "TO-0008", "status": "initiated", "items": [], "payments": [], "total_amount": 2500.0}}, response_only=True, status_codes=["200"])],
)
@skip_subscription_validation
@skip_feature_validation
def retrieve(self, request, pk=None):
    """
    Retrieve a single Tailor Order with detailed nested info.
    """
    try:
        # Validate pk is numeric
        try:
            order_id = int(pk)
        except (ValueError, TypeError):
            return self.validation_error_response(
                message="Invalid order ID provided.",
                dev_message=f"pk is not a valid integer: {pk}"
            )

        # Fetch order with optimized query
        try:
            order = TailorOrder.objects.select_related(
                "shop", "client", "created_by"
            ).prefetch_related(
                Prefetch(
                    "items",
                    queryset=TailorOrderItem.objects.select_related("assigned_tailor")
                    .prefetch_related(
                        Prefetch(
                            "assignments",
                            queryset=TailorOrderItemAssignment.objects.select_related("tailor"),
                        ),
                        Prefetch(
                            "material_usages",
                            queryset=TailorMaterialUsage.objects.select_related(
                                "material__product"
                            ),
                        ),
                    ),
                ),
                "additional_charges",
                Prefetch("payments", queryset=CustomerPayment.objects.select_related("account").order_by("created_at", "id")),
                Prefetch("security_payments", queryset=TailorOrderSecurityPayment.objects.select_related("account").order_by("created_at")),
                Prefetch("refund_entries", queryset=TailorOrderRefund.objects.select_related("account").order_by("created_at", "id")),
                Prefetch(
                    "security_adjustments",
                    queryset=TailorOrderSecurityDepositAdjustment.objects.select_related("account").order_by("-created_at"),
                ),
            ).get(
                pk=order_id,
                shop_id=self.shop_id,
                deleted__isnull=True
            )

        except TailorOrder.DoesNotExist:
            return self.not_found_response(
                resource="Tailor Order",
                message="Tailor order not found.",
                dev_message=f"Order {order_id} not found in shop {self.shop_id} or has been deleted"
            )

        # Serialize the order
        serializer = TailorOrderDetailSerializer(order)

        items = list(order.items.all())
        additional_charges = list(order.additional_charges.all())
        payments_qs = list(order.payments.all())
        security_payments_qs = list(order.security_payments.all())
        refunds_qs = list(order.refund_entries.all())
        security_adjustments_qs = list(order.security_adjustments.all())

        def _to_decimal(value):
            try:
                return Decimal(str(value or 0))
            except Exception:
                return Decimal("0")

        def _security_method(account_obj):
            if account_obj and getattr(account_obj, "account_type", None) == "cash":
                return "cash"
            if account_obj:
                return "upi"
            first_payment = payments_qs[0] if payments_qs else None
            return (getattr(first_payment, "payment_method", None) or "cash")

        # Security payment
        _sp = security_payments_qs[0] if security_payments_qs else None
        security_amount_is_paid = bool(_sp.is_paid) if _sp else True
        security = {
            "id": _sp.id,
            "amount": float(_sp.amount),
            "account_id": _sp.account_id,
            "account_name": _sp.account.name if _sp.account_id else None,
            "security_amount_is_paid": security_amount_is_paid,
            "paid_at": localtime(_sp.paid_at).strftime("%d-%m-%Y %H:%M:%S") if _sp.paid_at else None,
            "created_at": localtime(_sp.created_at).strftime("%d-%m-%Y %H:%M:%S") if _sp.created_at else None,
        } if _sp else None

        security_amount_dec = _to_decimal(order.security_amount)

        # Fallback for legacy rows: security exists on order but no security payment row.
        if not _sp and security_amount_dec > 0:
            cash_account = (
                Account.objects.filter(
                    shop_id=self.shop_id,
                    account_type="cash",
                    is_active=True,
                    deleted__isnull=True,
                )
                .order_by("-is_default", "id")
                .first()
            )
            security = {
                "id": -1,
                "amount": float(security_amount_dec),
                "account_id": cash_account.id if cash_account else None,
                "account_name": cash_account.name if cash_account else None,
                "security_amount_is_paid": security_amount_is_paid,
                "created_at": localtime(order.created_at).strftime("%d-%m-%Y %H:%M:%S") if order.created_at else None,
            }

        # Merge security rows into payments history with type and created_at sort.
        payments_history = [
            {
                "id": p.id,
                "payment_amount": int(p.payment_amount or 0),
                "payment_method": p.payment_method,
                "account_id": p.account_id,
                "account_name": p.account.name if p.account_id else None,
                "created_at": localtime(p.created_at).strftime("%d-%m-%Y %H:%M:%S") if p.created_at else None,
                "type": "payment",
                "_created_at": p.created_at,
            }
            for p in payments_qs
        ]

        for sp in security_payments_qs:
            if not sp.is_paid:
                continue

            payments_history.append(
                {
                    "id": sp.id,
                    "payment_amount": float(sp.amount or 0),
                    "payment_method": _security_method(sp.account),
                    "account_id": sp.account_id,
                    "account_name": sp.account.name if sp.account_id else None,
                    "created_at": localtime(sp.paid_at).strftime("%d-%m-%Y %H:%M:%S") if sp.paid_at else localtime(sp.created_at).strftime("%d-%m-%Y %H:%M:%S") if sp.created_at else None,
                    "type": "security",
                    "_created_at": sp.created_at,
                }
            )

        if not security_payments_qs and security_amount_dec > 0:
            if security_amount_is_paid:
                payments_history.append(
                    {
                        "id": -1,
                        "payment_amount": float(security_amount_dec),
                        "payment_method": "cash",
                        "account_id": security.get("account_id") if security else None,
                        "account_name": security.get("account_name") if security else None,
                        "created_at": security.get("created_at") if security else None,
                        "type": "security",
                        "_created_at": order.created_at,
                    }
                )

        payments_history.sort(key=lambda row: (row["_created_at"] is None, row["_created_at"]))
        for row in payments_history:
            row.pop("_created_at", None)

        refunds = [
            {
                "id": r.id,
                "refunded_amount": float(r.refunded_amount or 0),
                "refund_method": r.refund_method,
                "account_id": r.account_id,
                "account_name": r.account.name if r.account_id else None,
                "refund_reason": r.refund_reason,
                "created_at": localtime(r.created_at).strftime("%d-%m-%Y %H:%M:%S") if r.created_at else None,
            }
            for r in refunds_qs
        ]

        total_refunded_dec = sum((_to_decimal(r.refunded_amount) for r in refunds_qs), Decimal("0"))

        # -------- Security Adjustments --------
        security_adjustments = []
        total_security_refunded = Decimal("0")
        total_security_deducted = Decimal("0")

        for adj in security_adjustments_qs:
            if adj.adjustment_type == "refunded":
                total_security_refunded += _to_decimal(adj.amount)
                adjustment_type = "refund"
            elif adj.adjustment_type == "deducted":
                total_security_deducted += _to_decimal(adj.amount)
                adjustment_type = "deduction"

            security_adjustments.append({
                "id": adj.id,
                "adjustment_type": adjustment_type,
                "amount": float(adj.amount),
                "payment_method": adj.payment_method,
                "account_id": adj.account_id,
                "account_name": adj.account.name if adj.account_id else None,
                "reason": adj.reason,
                "created_at": localtime(adj.created_at).strftime("%d-%m-%Y %H:%M:%S") if adj.created_at else None,
            })

        security_remaining = security_amount_dec - (total_security_refunded + total_security_deducted)
        if security_remaining < 0:
            security_remaining = Decimal("0")

        total_product_charge_dec = sum((_to_decimal(i.customer_item_charge) for i in items), Decimal("0"))
        total_additional_charges_dec = sum((_to_decimal(c.amount) for c in additional_charges), Decimal("0"))
        total_tailor_charges_dec = _to_decimal(order.total_tailor_charges)
        paid_amount_dec = _to_decimal(order.paid_amount)
        discount_amount_dec = _to_decimal(order.discount_amount)

        total_material_cost_dec = Decimal("0")
        for item in items:
            for material in item.material_usages.all():
                total_material_cost_dec += _to_decimal(material.total_amount)

        total_amount_dec = total_product_charge_dec + total_additional_charges_dec
        if order.customer_order_type == "shop":
            total_amount_dec += total_tailor_charges_dec

        # Only include security in calculations if it's actually paid
        effective_security = security_amount_dec if security_amount_is_paid else Decimal("0")
        tax_amount_dec = _to_decimal(order.tax_amount)
        # Exclusive tax is additive on top of the raw (unmutated) item/charge
        # amounts, so it must be added here. Inclusive tax is already
        # reflected in what the customer pays for those raw amounts, so
        # adding tax_amount again here would double-count it.
        applicable_tax_dec = tax_amount_dec if order.tax_calculation_type == "exclusive" else Decimal("0")
        total_amount_with_security_dec = total_product_charge_dec + total_additional_charges_dec + security_amount_dec
        total_payable_dec = total_amount_with_security_dec + applicable_tax_dec - discount_amount_dec
        total_paid_with_security_dec = paid_amount_dec + effective_security
        balance_dec = total_amount_with_security_dec + applicable_tax_dec - total_paid_with_security_dec - discount_amount_dec

        return self.success_response(
            data={
                **serializer.data,
                "security_amount_is_paid": security_amount_is_paid,
                "payments": payments_history,
                "refunds": refunds,
                "total_refunded": float(total_refunded_dec),
                "security": security,
                "security_adjustments": security_adjustments,
                "security_summary": {
                    "security_amount": float(security_amount_dec),
                    "total_refunded": float(total_security_refunded),
                    "total_deducted": float(total_security_deducted),
                    "remaining_balance": float(security_remaining),
                },
                "total_product_charge": float(total_product_charge_dec),
                "total_material_cost": float(total_material_cost_dec),
                "total_amount": float(total_amount_dec),
                "total_amount_with_security": float(total_amount_with_security_dec),
                "total_payable": float(total_payable_dec),
                "total_paid_with_security": float(total_paid_with_security_dec),
                "balance": float(balance_dec),
                "tax": {
                    "is_tax_applied": bool(order.is_tax_applied),
                    "tax_name": order.tax_name or None,
                    "tax_rate": float(order.tax_rate or 0),
                    "tax_calculation_type": order.tax_calculation_type or None,
                    "taxable_components": order.taxable_components or [],
                    "subtotal": float(order.subtotal or 0),
                    "taxable_amount": float(order.taxable_amount or 0),
                    "tax_amount": float(order.tax_amount or 0),
                },
            },
            message="Tailor order details retrieved successfully.",
            dev_message=f"Retrieved order {order_id} for shop {self.shop_id}"
        )

    except Exception as e:
        logger.error(f"Error retrieving tailor order {pk} for shop {self.shop_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve tailor order details. Please try again later.",
            dev_message=str(e)
        )
