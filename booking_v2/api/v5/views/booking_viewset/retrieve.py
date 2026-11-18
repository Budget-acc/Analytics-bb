import logging
from decimal import Decimal
from django.utils.timezone import localtime
from django.db.models import Prefetch
from booking_v2.models import (
    BookingV2, BookedProductVariant, PaymentDetailsV2, RefundDetails,
    SecurityDepositPayment, SecurityDepositAdjustment,
)
from finance.models import Account
from services.models import ServiceAttributeValue
from booking_v2.api.v5.serializers import (
    BookingDetailSerializer, BookedProductVariantSerializer,
    BookingAdditionalChargeSerializer,
)
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Retrieve booking details",
    description=(
        "Returns a booking with booked items, payments, refunds, security adjustments, "
        "requirements, and tax details.\n\n"
        "The `tax` object reflects the tax snapshot recorded on the booking at the time it "
        "was created or last updated (immutable - unaffected by later changes to the shop's "
        "tax configuration). `is_tax_applied=false` means no tax was applied to this "
        "booking, in which case the other tax fields are zero/blank. `total_amount` "
        "(top-level) and `total_payable` already include `tax_amount` when tax is applied.\n\n"
        "`booked_items[].measurements` is returned in the structure and key order it was saved in: "
        "flat key-value pairs (e.g. {\"chest\": \"46\"}) or measurements grouped per person "
        "(e.g. {\"Person 1\": {\"chest\": \"46\", \"sleeve_length\": \"4\"}})."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description="Booking ID.",
        )
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Booking details fetched successfully."),
        404: OpenApiResponse(description="Exact message: Booking not found."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching booking details."),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        shop_id = self.shop_id

        booking = (
            BookingV2.objects.select_related(
                "client_identity",
                "service__general_service__main_category",
                "staff",
            )
            .prefetch_related(
                Prefetch(
                    "booked_items",
                    queryset=BookedProductVariant.objects.select_related(
                        "product_variant__product__shop_service"
                    )
                ),
                Prefetch(
                    "booked_items__product_variant__product__shop_service__general_service__main_category"
                ),
                Prefetch(
                    "booked_items__product_variant__product__attribute_values",
                    queryset=ServiceAttributeValue.objects.select_related("attribute")
                ),
                Prefetch(
                    "booked_items__product_variant__attribute_values",
                    queryset=ServiceAttributeValue.objects.select_related("attribute")
                ),
                Prefetch("requirements"),
                Prefetch("additional_charges"),
                Prefetch("payments", queryset=PaymentDetailsV2.objects.select_related("account").order_by("created_at")),
                Prefetch("refunds", queryset=RefundDetails.objects.select_related("account").order_by("created_at")),
                Prefetch(
                    "security_deposit_payments",
                    queryset=SecurityDepositPayment.objects.select_related("account").order_by("-created_at")
                ),
                Prefetch(
                    "security_adjustments",
                    queryset=SecurityDepositAdjustment.objects.select_related("account").order_by("-created_at")
                ),
            )
            .filter(id=pk, shop_id=shop_id)
            .first()
        )

        if not booking:
            return self.not_found_response(
                resource="Booking",
                message="Booking not found.",
                dev_message=f"Booking with id {pk} not found for shop {shop_id}."
            )

        # Booking details
        booking_data = BookingDetailSerializer(booking).data

        # Booked items
        booked_items = BookedProductVariantSerializer(
            booking.booked_items.all(), many=True
        ).data

        # Additional charges
        additional_charges = BookingAdditionalChargeSerializer(
            booking.additional_charges.all(), many=True
        ).data

        # Payments history (prefetched)
        payments_qs = list(getattr(booking, "payments").all())
        payments = [
            {
                "id": p.id,
                "amount": int(p.payment_amount),
                "payment_method": p.payment_method,
                "account_id": p.account_id,
                "account_name": p.account.name if p.account_id else None,
                "created_at": localtime(p.created_at).strftime("%d-%m-%Y %H:%M:%S") if p.created_at else None,
                "_created_at": p.created_at,
                "type": "payments",
            }
            for p in payments_qs
        ]

        security_amount = Decimal(str(booking.security_amount or 0))

        _sp = next(iter(booking.security_deposit_payments.all()), None)
        security = {
            "id": _sp.id,
            "amount": float(_sp.amount),
            "payment_method": _sp.payment_method,
            "account_id": _sp.account_id,
            "account_name": _sp.account.name if _sp.account_id else None,
            "security_amount_is_paid": bool(_sp.is_paid),
            "paid_at": localtime(_sp.paid_at).strftime("%d-%m-%Y %H:%M:%S") if _sp.paid_at else None,
            "created_at": localtime(_sp.created_at).strftime("%d-%m-%Y %H:%M:%S") if _sp.created_at else None,
        } if _sp else None

        security_payment_method = _sp.payment_method if _sp else None
        security_account_id = _sp.account_id if _sp else None
        security_account_name = _sp.account.name if _sp and _sp.account_id else None
        security_amount_is_paid = bool(_sp.is_paid) if _sp else True

        # Fallback for legacy bookings: security amount exists but no security payment row.
        if not _sp and security_amount > 0:
            cash_account = (
                Account.objects.filter(
                    shop_id=shop_id,
                    account_type="cash",
                    is_active=True,
                    deleted__isnull=True,
                )
                .order_by("-is_default", "id")
                .first()
            )

            security = {
                "id": -1,
                "amount": float(security_amount),
                "payment_method": "cash",
                "account_id": cash_account.id if cash_account else None,
                "account_name": cash_account.name if cash_account else None,
                "created_at": localtime(booking.created_at).strftime("%d-%m-%Y %H:%M:%S") if booking.created_at else None,
            }

            security_payment_method = "cash"
            security_account_id = cash_account.id if cash_account else None
            security_account_name = cash_account.name if cash_account else None
            security_amount_is_paid = True

        # -------- Security Adjustments --------
        adjustments_qs = list(getattr(booking, "security_adjustments").all())

        security_adjustments = []
        total_security_refund = Decimal("0")
        total_security_deduction = Decimal("0")

        for adj in adjustments_qs:
            if adj.adjustment_type == "refund":
                total_security_refund += adj.amount
            elif adj.adjustment_type == "deduction":
                total_security_deduction += adj.amount

            security_adjustments.append(
                {
                    "id": adj.id,
                    "type": adj.adjustment_type,
                    "amount": float(adj.amount),
                    "payment_method": adj.payment_method,
                    "account_id": adj.account_id,
                    "account_name": adj.account.name if adj.account_id else None,
                    "reason": adj.reason,
                    "created_at": localtime(adj.created_at).strftime("%d-%m-%Y %H:%M:%S")
                    if adj.created_at else None
                }
            )

        security_remaining = security_amount - (
            total_security_refund + total_security_deduction
        )

        if security_remaining < 0:
            security_remaining = Decimal("0")

        # Unpaid deposit: no money is held, so nothing is shown as collected,
        # refunded, deducted or remaining.
        if security_amount_is_paid:
            security_summary = {
                "security_amount": float(security_amount),
                "total_refunded": float(total_security_refund),
                "total_deducted": float(total_security_deduction),
                "remaining_balance": float(security_remaining),
            }
        else:
            security_summary = {
                "security_amount": 0.0,
                "total_refunded": 0.0,
                "total_deducted": 0.0,
                "remaining_balance": 0.0,
            }

        # Refunds history (prefetched). refunded_amount is DecimalField.
        refunds_qs = list(getattr(booking, "refunds").all())
        total_refunded = sum((r.refunded_amount or Decimal("0")) for r in refunds_qs)
        refunds = [
            {
                "id": r.id,
                "refunded_amount": float(r.refunded_amount),
                "refund_method": r.refund_method,
                "account_id": r.account_id,
                "account_name": r.account.name if r.account_id else None,
                "refund_reason": r.refund_reason,
                "created_at": localtime(r.created_at).strftime("%d-%m-%Y %H:%M:%S") if r.created_at else None
            }
            for r in refunds_qs
        ]
        # advance_amount is always the net paid amount (decremented on each refund for all booking types).
        # total_refunded reflects all refunds regardless of booking status.
        advance_dec = Decimal(int(booking.advance_amount or 0))
        total_refunded = sum((r.refunded_amount or Decimal("0")) for r in refunds_qs)
        # advance_amount is net (decremented on each refund), so it IS the refundable balance.
        # total_refunded is shown separately in the response for display purposes only.
        # Do NOT subtract total_refunded here — that would double-count the deduction.
        refundable_balance_dec = max(advance_dec, Decimal(0))

        total_refunded_f = float(total_refunded)
        refundable_balance = float(refundable_balance_dec)

        # Merge security into the payments timeline and sort by created_at.
        effective_security = security_amount if security_amount_is_paid else Decimal("0")
        invoice_total_dec = Decimal(str(booking.total_amount or 0))
        total_amount_with_security_dec = invoice_total_dec + effective_security
        total_payable_dec = (invoice_total_dec + security_amount) - Decimal(str(booking.discount_amount or 0))
        advance_amount_with_security_dec = Decimal(str(booking.advance_amount or 0)) + effective_security
        balance_dec = total_payable_dec - advance_amount_with_security_dec

        if security and float(security.get("amount") or 0) > 0 and security_amount_is_paid:
            security_created_at = _sp.created_at if _sp else booking.created_at
            payments.append(
                {
                    "id": security.get("id"),
                    "amount": float(security.get("amount") or 0),
                    "payment_method": security.get("payment_method"),
                    "account_id": security.get("account_id"),
                    "account_name": security.get("account_name"),
                    "created_at": security.get("paid_at") or security.get("created_at"),
                    "_created_at": security_created_at,
                    "type": "security",
                }
            )

        payments.sort(key=lambda row: (row["_created_at"] is None, row["_created_at"]))
        for row in payments:
            row.pop("_created_at", None)

        payload = {
            **booking_data,
            "security_payment_method": security_payment_method,
            "security_amount_is_paid": security_amount_is_paid,
            "security_account_id": security_account_id,
            "security_account_name": security_account_name,
            "booked_items": booked_items,
            "additional_charges": additional_charges,
            "payments": payments,
            "security": security,
            "security_adjustments": security_adjustments,
            "security_summary": security_summary,
            "refunds": refunds,
            "total_refunded": total_refunded_f,
            "refundable_balance": refundable_balance,
            "total_amount_with_security": float(total_amount_with_security_dec),
            "advance_amount_with_security": float(advance_amount_with_security_dec),
            "total_payable": float(total_payable_dec),
            "balance": float(balance_dec),
            "tax": {
                "is_tax_applied": bool(booking.is_tax_applied),
                "tax_name": booking.tax_name or None,
                "tax_rate": float(booking.tax_rate or 0),
                "tax_calculation_type": booking.tax_calculation_type or None,
                "taxable_components": booking.taxable_components or [],
                "subtotal": float(booking.subtotal or 0),
                "taxable_amount": float(booking.taxable_amount or 0),
                "tax_amount": float(booking.tax_amount or 0),
            },
        }

        return self.success_response(
            data=payload,
            message="Booking details fetched successfully."
        )

    except Exception as e:
        logger.error(f"Booking retrieve error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching booking details.",
            dev_message=str(e)
        )


__all__ = ["retrieve"]