import logging
from datetime import datetime
from decimal import Decimal

from django.db.models import (
    Q, Sum, F, ExpressionWrapper, IntegerField, DecimalField, Value,
    Exists, OuterRef, Subquery, CharField,
)
from django.db.models.functions import Coalesce, Cast
from django.utils.timezone import localdate
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from booking_v2.models import (
    BookingV2, Client, PaymentDetailsV2, RefundDetails,
    SecurityDepositAdjustment, SecurityDepositPayment,
)
from expenses_v2.models import Expenses, ProductVariantExpense
from sales.models import SalesPaymentDetails
from shop.api.v3.utils.shop import get_active_shop_id
from tailored.models import TailorOrder, CustomerPayment
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class DaywiseShopSummaryAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Expense Management V3"],
        summary="Get day-wise paid summary",
        description=(
            "Returns day-wise rollup for paid flows: incoming payments and outgoing expenses, plus pending/security/booking totals. "
            "Use this for wallet day summary and paid cashflow snapshot cards."
        ),
        parameters=[
            OpenApiParameter(name="date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Date filter. Formats: YYYY-MM-DD or DD-MM-YYYY. Default: today."),
            OpenApiParameter(name="client_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional client-specific summary."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Daywise summary fetched successfully."),
            400: OpenApiResponse(description="Exact message: Invalid date format."),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            404: OpenApiResponse(description="Exact message: Client not found."),
            500: OpenApiResponse(description="Exact message: Something went wrong while fetching the daywise shop summary."),
        },
    )
    def get(self, request):
        try:
            try:
                shop_id = get_active_shop_id(request)
            except Exception as e:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated.",
                )

            # Handle date param or default to today
            date_str = request.query_params.get("date")
            if date_str:
                parsed = False
                for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        date_obj = datetime.strptime(date_str, fmt).date()
                        parsed = True
                        break
                    except ValueError:
                        continue
                if not parsed:
                    return self.validation_error_response(
                        errors={"date": ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY"]},
                        message="Invalid date format."
                    )
            else:
                date_obj = localdate()

            client_id = request.query_params.get("client_id")

            if client_id:
                try:
                    client = Client.objects.get(id=client_id, shop_id=shop_id, deleted__isnull=True)
                    client_filter_booking = Q(client_identity_id=client_id)
                    client_filter_payment = Q(booking__client_identity_id=client_id)
                except Client.DoesNotExist:
                    return self.not_found_response(
                        resource="Client",
                        message="Client not found.",
                        dev_message=f"Client with id {client_id} not found for shop {shop_id}."
                    )
            else:
                client_filter_booking = Q()
                client_filter_payment = Q()

            # ---------- EXPENSE SUMMARY ----------
            general_expense = Expenses.objects.filter(
                shop_id=shop_id, date=date_obj
            ).aggregate(total=Sum("expense"))["total"] or 0

            variant_expense = ProductVariantExpense.objects.filter(
                shop_id=shop_id, date=date_obj
            ).aggregate(total=Sum("expense"))["total"] or 0

            total_expense = general_expense + variant_expense

            # ---------- PAYMENT SUMMARY ----------
            booking_payments = PaymentDetailsV2.objects.filter(
                booking__shop_id=shop_id,
                booking__deleted__isnull=True,
                created_at__date=date_obj
            ).exclude(
                booking__delivery_status="cancelled"
            ).filter(client_filter_payment).aggregate(
                gpay_total=Sum("payment_amount", filter=Q(payment_method__in=["gpay", "upi"])),
                cash_total=Sum("payment_amount", filter=Q(payment_method="cash")),
                total=Sum("payment_amount"),
            )

            cancelled_bookings = BookingV2.objects.filter(
                shop_id=shop_id,
                delivery_status="cancelled",
                deleted__isnull=True,
                created_at__date=date_obj
            ).filter(client_filter_booking).annotate(
                total_refund=Coalesce(
                    Subquery(
                        RefundDetails.objects.filter(booking=OuterRef('pk'))
                        .values('booking')
                        .annotate(total=Sum('refunded_amount'))
                        .values('total')[:1]
                    ),
                    Value(0),
                    output_field=DecimalField(max_digits=10, decimal_places=2)
                ),
                sample_method=Subquery(
                    PaymentDetailsV2.objects.filter(booking=OuterRef('pk'))
                    .order_by('-created_at')
                    .values('payment_method')[:1]
                )
            ).annotate(
                penalty_amount=ExpressionWrapper(
                    Cast(F('advance_amount'), DecimalField(max_digits=10, decimal_places=2))
                    - F('total_refund'),
                    output_field=DecimalField(max_digits=10, decimal_places=2)
                ),
                payment_method_final=Coalesce(
                    F("sample_method"),
                    Value("cash"),
                    output_field=CharField()
                )
            ).filter(penalty_amount__gt=0)

            penalty_payments = cancelled_bookings.aggregate(
                gpay_total=Sum("penalty_amount", filter=Q(payment_method_final__in=["gpay", "upi"])),
                cash_total=Sum("penalty_amount", filter=Q(payment_method_final="cash")),
                total=Sum("penalty_amount")
            )

            sales_payments = SalesPaymentDetails.objects.filter(
                sale__shop_id=shop_id,
                created_at__date=date_obj
            )
            if client_id:
                sales_payments = sales_payments.filter(sale__client_id=client_id)
            sales_payments = sales_payments.aggregate(
                gpay_total=Sum("payment_amount", filter=Q(payment_method__in=["gpay", "upi"])),
                cash_total=Sum("payment_amount", filter=Q(payment_method="cash")),
                total=Sum("payment_amount"),
            )

            tailor_payments = CustomerPayment.objects.filter(
                order__shop_id=shop_id,
                order__deleted__isnull=True,
                created_at__date=date_obj
            )
            if client_id:
                tailor_payments = tailor_payments.filter(order__client_id=client_id)
            tailor_payments = tailor_payments.aggregate(
                gpay_total=Sum("payment_amount", filter=Q(payment_method__in=["gpay", "upi"])),
                cash_total=Sum("payment_amount", filter=Q(payment_method="cash")),
                total=Sum("payment_amount"),
            )

            payment_summary = {
                "gpay_total": (
                    (booking_payments["gpay_total"] or 0) +
                    (penalty_payments["gpay_total"] or 0) +
                    (sales_payments["gpay_total"] or 0) +
                    (tailor_payments["gpay_total"] or 0)
                ),
                "cash_total": (
                    (booking_payments["cash_total"] or 0) +
                    (penalty_payments["cash_total"] or 0) +
                    (sales_payments["cash_total"] or 0) +
                    (tailor_payments["cash_total"] or 0)
                ),
                "total": (
                    (booking_payments["total"] or 0) +
                    (penalty_payments["total"] or 0) +
                    (sales_payments["total"] or 0) +
                    (tailor_payments["total"] or 0)
                )
            }

            # ---------- PENDING SUMMARY ----------
            pending_expression = ExpressionWrapper(
                F("total_amount") - F("advance_amount") - F("discount_amount"),
                output_field=IntegerField()
            )
            pending_total = BookingV2.objects.filter(
                shop_id=shop_id,
                payment_status=False,
                deleted__isnull=True
            ).exclude(
                delivery_status="cancelled"
            ).annotate(
                net_pending=F("total_amount") - F("advance_amount") - F("discount_amount")
            ).filter(
                net_pending__gt=0
            ).aggregate(
                total_pending=Sum(pending_expression)
            )["total_pending"] or 0

            # ---------- SECURITY AMOUNT SUMMARY ----------
            bookings_with_security = BookingV2.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                deleted__isnull=True,
                security_deposit_payments__is_paid=True,
            ).exclude(delivery_status='cancelled').distinct()

            if client_id:
                bookings_with_security = bookings_with_security.filter(client_identity_id=client_id)

            booking_security_total = bookings_with_security.aggregate(
                total=Coalesce(
                    Sum('security_amount'),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            )['total'] or Decimal('0')

            adjustments_summary = SecurityDepositAdjustment.objects.filter(
                booking__in=bookings_with_security
            ).aggregate(
                total_refunded=Coalesce(
                    Sum('amount', filter=Q(adjustment_type='refund')),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                ),
                total_deducted=Coalesce(
                    Sum('amount', filter=Q(adjustment_type='deduction')),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            )

            booking_refunded_from_adjustments = adjustments_summary['total_refunded'] or Decimal('0')
            booking_deducted = adjustments_summary['total_deducted'] or Decimal('0')

            completed_without_entries = bookings_with_security.filter(
                booking_status='completed'
            ).annotate(
                has_adjustments=Exists(
                    SecurityDepositAdjustment.objects.filter(booking_id=OuterRef('id'))
                ),
                has_payments=Exists(
                    SecurityDepositPayment.objects.filter(booking_id=OuterRef('id'), is_paid=True)
                )
            ).filter(has_adjustments=False, has_payments=False)

            completed_refund_total = completed_without_entries.aggregate(
                total=Coalesce(
                    Sum('security_amount'),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            )['total'] or Decimal('0')

            booking_refunded = booking_refunded_from_adjustments + completed_refund_total
            booking_pending = booking_security_total - (booking_refunded + booking_deducted)
            if booking_pending < 0:
                booking_pending = Decimal('0')

            tailor_orders_with_security = TailorOrder.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                created_booking__isnull=True,
                deleted__isnull=True
            )
            if client_id:
                tailor_orders_with_security = tailor_orders_with_security.filter(client_id=client_id)

            tailor_security_total = tailor_orders_with_security.aggregate(
                total=Coalesce(
                    Sum('security_amount'),
                    Value(Decimal('0')),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            )['total'] or Decimal('0')

            tailor_pending = tailor_security_total

            total_security_amount = booking_security_total + tailor_security_total
            total_refunded_amount = booking_refunded
            total_deducted_amount = booking_deducted
            total_pending_amount = booking_pending + tailor_pending

            # ---------- BOOKINGS SUMMARY ----------
            bookings_total = BookingV2.objects.filter(
                shop_id=shop_id,
                created_at__date=date_obj
            ).filter(client_filter_booking).aggregate(
                total_bookings=Sum("total_amount")
            )["total_bookings"] or 0

            return self.success_response(
                data={
                    "date": date_obj.strftime("%d-%m-%Y"),
                    "payments": {
                        "gpay": payment_summary["gpay_total"] or 0,
                        "cash": payment_summary["cash_total"] or 0,
                        "total": payment_summary["total"] or 0,
                    },
                    "expenses": {
                        "total": total_expense
                    },
                    "pendings": {
                        "total": pending_total
                    },
                    "security": {
                        "total": float(total_security_amount),
                        "total_refunded": float(total_refunded_amount),
                        "total_deducted": float(total_deducted_amount),
                        "total_pending": float(total_pending_amount),
                    },
                    "bookings": {
                        "total": bookings_total
                    }
                },
                message="Daywise summary fetched successfully."
            )

        except Exception as e:
            logger.error(f"DaywiseShopSummaryAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching the daywise shop summary.",
                dev_message=str(e)
            )
