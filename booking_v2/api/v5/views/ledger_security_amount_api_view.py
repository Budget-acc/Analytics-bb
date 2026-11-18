import logging
from collections import defaultdict, OrderedDict
from datetime import datetime
from decimal import Decimal
from django.db.models import Prefetch, Sum, Count, Q, DecimalField, Case, When, F, Value, OuterRef, Subquery, IntegerField, CharField
from django.db.models.functions import Coalesce
from django.utils.timezone import localtime, localdate
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import (
    BookingV2, Client, SecurityDepositPayment, SecurityDepositAdjustment,
)
from tailored.models import (
    TailorOrder, TailorOrderSecurityPayment,
    TailorOrderSecurityDepositAdjustment,
)

ADJUSTMENT_TYPES = {
    "BOOKING_REFUND": "refund",
    "BOOKING_DEDUCTION": "deduction",
    "TAILOR_REFUNDED": "refunded",
    "TAILOR_DEDUCTED": "deducted",
}
from booking_v2.api.v5.views.pagination import DayGroupedPagination
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class LedgerSecurityAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Booking Management V5"],
        summary="Get security amount ledger",
        description=(
            "Returns booking and tailor-order security amounts grouped by Month -> Day with refunded and deducted values."
        ),
        parameters=[
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Optional client filter.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Grouped day-wise security ledger response."),
            400: OpenApiResponse(
                description="Validation errors.\n\nExact messages:\n- Client ID must be a valid integer.\n- Client not found for this shop."
            ),
            403: OpenApiResponse(
                description="Permission error.\n\nExact message:\n- Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Server error.\n\nExact message:\n- Something went wrong while fetching security amounts."
            ),
        },
    )
    def get(self, request):
        try:
            # -------- Validate shop --------
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )

            # -------- Validate client (optional) --------
            client_id = request.query_params.get("client_id")
            if client_id:
                if not client_id.isdigit():
                    return self.validation_error_response(
                        errors={"client_id": ["Client ID must be a valid integer."]},
                        message="Client ID must be a valid integer."
                    )
                try:
                    client = Client.objects.get(id=client_id, shop_id=shop_id)
                except Client.DoesNotExist:
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found for this shop."]},
                        message="Client not found for this shop."
                    )
            else:
                client = None

            # -------- Get ALL bookings with a paid security deposit --------
            booking_query = BookingV2.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                deleted__isnull=True,
                return_date__isnull=False,
                return_date__date__lte=localdate(),
                security_deposit_payments__is_paid=True,
            ).exclude(
                delivery_status="cancelled"
            ).distinct().select_related(
                "client_identity"
            ).prefetch_related(
                Prefetch(
                    "security_deposit_payments",
                    queryset=SecurityDepositPayment.objects.filter(is_paid=True).order_by('-created_at'),
                    to_attr="_prefetched_security_payments"
                ),
                Prefetch(
                    "security_adjustments",
                    queryset=SecurityDepositAdjustment.objects.all(),
                    to_attr="_prefetched_security_adjustments"
                )
            )

            if client:
                booking_query = booking_query.filter(client_identity=client)

            # Exclude bookings that were created from converted tailor orders
            converted_booking_ids = TailorOrder.objects.filter(
                shop_id=shop_id,
                created_booking__isnull=False,
            ).values_list("created_booking_id", flat=True)
            booking_query = booking_query.exclude(id__in=converted_booking_ids)

            # -------- Tailor orders with security amount --------
            tailor_query = TailorOrder.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                customer_order_type="rental",
                created_booking__isnull=True,
                deleted__isnull=True,
                return_date__isnull=False,
                return_date__date__lte=localdate(),
                security_payments__is_paid=True,
            ).select_related("client").prefetch_related(
                Prefetch(
                    "security_payments",
                    queryset=TailorOrderSecurityPayment.objects.filter(is_paid=True).order_by('-created_at'),
                    to_attr="_prefetched_security_payments"
                ),
                Prefetch(
                    "security_adjustments",
                    queryset=TailorOrderSecurityDepositAdjustment.objects.all(),
                    to_attr="_prefetched_security_adjustments"
                )
            )

            if client:
                tailor_query = tailor_query.filter(client=client)

            # -------- Process bookings with refund calculation and payment method --------
            combined_data = []

            for booking in booking_query:
                created = localtime(booking.created_at) if booking.created_at else None
                booking_sort_at = localtime(booking.return_date) if booking.return_date else created

                # -------- Adjustment Ledger --------
                adjustments = getattr(booking, "_prefetched_security_adjustments", None)
                if adjustments is None:
                    adjustments = booking.security_adjustments.all()

                total_refunded = Decimal("0")
                total_deducted = Decimal("0")

                for adj in adjustments:
                    if adj.adjustment_type == ADJUSTMENT_TYPES["BOOKING_REFUND"]:
                        total_refunded += Decimal(str(adj.amount))
                    elif adj.adjustment_type == ADJUSTMENT_TYPES["BOOKING_DEDUCTION"]:
                        total_deducted += Decimal(str(adj.amount))

                # -------- Backward Compatibility --------
                security_amount = int(booking.security_amount or 0)

                if (
                    booking.booking_status == "completed"
                    and security_amount > 0
                    and not adjustments
                ):
                    total_refunded = Decimal(security_amount)
                    total_deducted = Decimal("0")

                # -------- Payment Method --------
                payment_method = None
                security_payments = getattr(booking, "_prefetched_security_payments", None)
                if security_payments is None:
                    security_payments = booking.security_deposit_payments.order_by("-created_at")

                if security_payments:
                    payment_method = security_payments[0].payment_method

                combined_data.append({
                    "id": booking.id,
                    "client_name": booking.client_identity.client_name if booking.client_identity else "Unknown Client",
                    "security_amount": security_amount,
                    "payment_method": payment_method,
                    "refunded_amount": float(total_refunded),
                    "deducted_amount": float(total_deducted),
                    "date": created.strftime("%d-%m-%Y") if created else None,
                    "time": created.strftime("%I:%M %p") if created else None,
                    "created_at": created.strftime("%d-%m-%Y %H:%M:%S") if created else None,
                    "sort_at": booking_sort_at.strftime("%d-%m-%Y %H:%M:%S") if booking_sort_at else None,
                    "type": "booking",
                })

            # -------- Process tailor order data --------
            for order in tailor_query:
                created = localtime(order.created_at) if order.created_at else None
                return_date = localtime(order.return_date) if order.return_date else None
                order_sort_at = return_date if return_date else created

                adjustments = getattr(order, "_prefetched_security_adjustments", None)
                if adjustments is None:
                    adjustments = order.security_adjustments.all()

                total_refunded = Decimal("0")
                total_deducted = Decimal("0")
                for adj in adjustments:
                    if adj.adjustment_type == ADJUSTMENT_TYPES["TAILOR_REFUNDED"]:
                        total_refunded += Decimal(str(adj.amount))
                    elif adj.adjustment_type == ADJUSTMENT_TYPES["TAILOR_DEDUCTED"]:
                        total_deducted += Decimal(str(adj.amount))

                security_amount = int(order.security_amount or 0)
                if (
                    order.status == "completed"
                    and security_amount > 0
                    and not adjustments
                ):
                    total_refunded = Decimal(security_amount)
                    total_deducted = Decimal("0")

                payment_method = None
                security_payments = getattr(order, "_prefetched_security_payments", None)
                if security_payments is None:
                    security_payments = order.security_payments.order_by("-created_at")

                if security_payments:
                    payment_method = security_payments[0].payment_method

                client_name = order.client.client_name if order.client else "Walk-in Customer"

                combined_data.append({
                    "id": order.id,
                    "client_name": client_name,
                    "security_amount": security_amount,
                    "payment_method": payment_method,
                    "refunded_amount": float(total_refunded),
                    "deducted_amount": float(total_deducted),
                    "date": return_date.strftime("%d-%m-%Y") if return_date else (created.strftime("%d-%m-%Y") if created else None),
                    "time": return_date.strftime("%I:%M %p") if return_date else (created.strftime("%I:%M %p") if created else None),
                    "created_at": created.strftime("%d-%m-%Y %H:%M:%S") if created else None,
                    "sort_at": order_sort_at.strftime("%d-%m-%Y %H:%M:%S") if order_sort_at else None,
                    "type": "tailor_order"
                })

            # -------- Sort by sort_at DESC (latest first) --------
            combined_data.sort(
                key=lambda x: datetime.strptime(x["sort_at"], "%d-%m-%Y %H:%M:%S") if x["sort_at"] else datetime.min,
                reverse=True
            )

            # -------- Grouping by Month - Day (with latest first within each day) --------
            grouped_by_date = defaultdict(list)
            for item in combined_data:
                if item["sort_at"]:
                    created = datetime.strptime(item["sort_at"], "%d-%m-%Y %H:%M:%S")
                    normalized_date = created.date()

                    day_key = normalized_date.strftime("%d-%m-%Y")
                    month_key = normalized_date.strftime("%B %Y")

                    # Remove sort_at from final output (only used for sorting)
                    final_item = {k: v for k, v in item.items() if k != "sort_at"}
                    grouped_by_date[(month_key, day_key)].append(final_item)

            # -------- Build sorted structure with DESCENDING order --------
            month_day_map = defaultdict(lambda: defaultdict(list))
            for (month, day), items in grouped_by_date.items():
                sorted_items = sorted(
                    items,
                    key=lambda x: (x["security_amount"], x["id"]),
                    reverse=True
                )
                month_day_map[month][day].extend(sorted_items)

            # -------- Reverse month order (latest first) and day order (latest first) --------
            sorted_grouped = OrderedDict()

            def parse_month_key(month_str):
                return datetime.strptime(month_str, "%B %Y")

            def parse_day_key(day_str):
                return datetime.strptime(day_str, "%d-%m-%Y")

            for month in sorted(month_day_map.keys(), key=parse_month_key, reverse=True):
                sorted_grouped[month] = OrderedDict()
                for day in sorted(month_day_map[month].keys(), key=parse_day_key, reverse=True):
                    day_items = month_day_map[month][day]
                    day_total = day_refunded = day_deducted = 0.0
                    # Always include cash and upi keys regardless of which methods appear
                    pending_by_method = {"cash": 0.0, "upi": 0.0}
                    for item in day_items:
                        day_total    += item["security_amount"]
                        day_refunded += item["refunded_amount"]
                        day_deducted += item["deducted_amount"]
                        method = item.get("payment_method")
                        if method:
                            item_pending = max(0.0, item["security_amount"] - item["refunded_amount"] - item["deducted_amount"])
                            pending_by_method[method] = pending_by_method.get(method, 0.0) + item_pending
                    day_pending = day_total - day_refunded - day_deducted
                    sorted_grouped[month][day] = {
                        "items": day_items,
                        "total": day_total,
                        "total_refunded": day_refunded,
                        "total_deducted": day_deducted,
                        "total_pending": day_pending,
                        "pending_breakdown": pending_by_method,
                    }

            # -------- Overall pending: DB aggregation across ALL dates (including future) --------
            # Bookings: all eligible records regardless of return_date direction
            overall_booking_qs = BookingV2.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                deleted__isnull=True,
                return_date__isnull=False,
                security_deposit_payments__is_paid=True,
            ).exclude(
                delivery_status="cancelled"
            ).exclude(
                id__in=converted_booking_ids
            ).distinct()

            if client:
                overall_booking_qs = overall_booking_qs.filter(client_identity=client)

            # Single query: security sum + backward-compat (completed + no adjustments = fully refunded)
            booking_overall = overall_booking_qs.annotate(
                adj_count=Count('security_adjustments', distinct=True)
            ).aggregate(
                total_security=Coalesce(
                    Sum('security_amount', output_field=DecimalField()), Decimal('0')
                ),
                backward_compat=Coalesce(
                    Sum(
                        Case(
                            When(
                                Q(booking_status='completed') & Q(adj_count=0),
                                then=F('security_amount')
                            ),
                            default=Value(0),
                            output_field=DecimalField()
                        )
                    ), Decimal('0')
                )
            )

            booking_adj_totals = SecurityDepositAdjustment.objects.filter(
                booking__in=overall_booking_qs
            ).aggregate(
                refunded=Coalesce(Sum('amount', filter=Q(adjustment_type=ADJUSTMENT_TYPES["BOOKING_REFUND"])), Decimal('0')),
                deducted=Coalesce(Sum('amount', filter=Q(adjustment_type=ADJUSTMENT_TYPES["BOOKING_DEDUCTION"])), Decimal('0')),
            )

            overall_booking_pending = (
                booking_overall['total_security']
                - booking_overall['backward_compat']
                - booking_adj_totals['refunded']
                - booking_adj_totals['deducted']
            )

            # Tailor orders: all eligible records regardless of return_date direction
            overall_tailor_qs = TailorOrder.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                customer_order_type="rental",
                created_booking__isnull=True,
                deleted__isnull=True,
                return_date__isnull=False,
                security_payments__is_paid=True,
            ).distinct()

            if client:
                overall_tailor_qs = overall_tailor_qs.filter(client=client)

            # Single query: security sum + backward-compat
            tailor_overall = overall_tailor_qs.annotate(
                adj_count=Count('security_adjustments', distinct=True)
            ).aggregate(
                total_security=Coalesce(
                    Sum('security_amount', output_field=DecimalField()), Decimal('0')
                ),
                backward_compat=Coalesce(
                    Sum(
                        Case(
                            When(
                                Q(status='completed') & Q(adj_count=0),
                                then=F('security_amount')
                            ),
                            default=Value(0),
                            output_field=DecimalField()
                        )
                    ), Decimal('0')
                )
            )

            tailor_adj_totals = TailorOrderSecurityDepositAdjustment.objects.filter(
                order__in=overall_tailor_qs
            ).aggregate(
                refunded=Coalesce(Sum('amount', filter=Q(adjustment_type=ADJUSTMENT_TYPES["TAILOR_REFUNDED"])), Decimal('0')),
                deducted=Coalesce(Sum('amount', filter=Q(adjustment_type=ADJUSTMENT_TYPES["TAILOR_DEDUCTED"])), Decimal('0')),
            )

            overall_tailor_pending = (
                tailor_overall['total_security']
                - tailor_overall['backward_compat']
                - tailor_adj_totals['refunded']
                - tailor_adj_totals['deducted']
            )

            # Clamp to 0 — guards against data inconsistency (refunds exceeding paid security)
            overall_pending_amount = float(max(Decimal('0'), overall_booking_pending + overall_tailor_pending))

            # -------- Overall pending breakdown by payment method (cash / upi) --------
            # Uses subquery annotations — single SQL per entity type, no N+1.

            # Booking subqueries
            booking_pm_sq = SecurityDepositPayment.objects.filter(
                booking=OuterRef('pk'), is_paid=True
            ).order_by('-created_at').values('payment_method')[:1]

            booking_adj_cnt_sq = SecurityDepositAdjustment.objects.filter(
                booking=OuterRef('pk')
            ).values('booking').annotate(_c=Count('id')).values('_c')[:1]

            booking_refunded_sq = SecurityDepositAdjustment.objects.filter(
                booking=OuterRef('pk'),
                adjustment_type=ADJUSTMENT_TYPES["BOOKING_REFUND"]
            ).values('booking').annotate(_s=Sum('amount')).values('_s')[:1]

            booking_deducted_sq = SecurityDepositAdjustment.objects.filter(
                booking=OuterRef('pk'),
                adjustment_type=ADJUSTMENT_TYPES["BOOKING_DEDUCTION"]
            ).values('booking').annotate(_s=Sum('amount')).values('_s')[:1]

            # Resolve to unique IDs before annotating — avoids JOIN duplication from
            # security_deposit_payments__is_paid=True filter producing multiple rows
            # per booking when a booking has more than one paid security deposit payment.
            booking_ids = list(overall_booking_qs.values_list('id', flat=True))
            booking_breakdown_rows = BookingV2.objects.filter(id__in=booking_ids).annotate(
                _pm=Subquery(booking_pm_sq, output_field=CharField()),
                _adj_cnt=Coalesce(Subquery(booking_adj_cnt_sq, output_field=IntegerField()), 0),
                _refunded=Coalesce(Subquery(booking_refunded_sq, output_field=DecimalField()), Decimal('0')),
                _deducted=Coalesce(Subquery(booking_deducted_sq, output_field=DecimalField()), Decimal('0')),
            ).values('security_amount', 'booking_status', '_pm', '_adj_cnt', '_refunded', '_deducted')

            pending_breakdown = {'cash': Decimal('0'), 'upi': Decimal('0')}
            for row in booking_breakdown_rows:
                if row['booking_status'] == 'completed' and row['_adj_cnt'] == 0:
                    pending = Decimal('0')
                else:
                    # No per-row clamp — allow negative so over-refunded rows correctly
                    # offset others, mirroring the single global clamp on overall_pending_amount.
                    pending = Decimal(str(row['security_amount'])) - row['_refunded'] - row['_deducted']
                # Normalise: 'cash' stays cash; everything else (upi, gpay, bank…) → upi
                raw_method = (row['_pm'] or '').strip().lower()
                bucket = 'cash' if raw_method == 'cash' else 'upi'
                pending_breakdown[bucket] += pending

            # Tailor order subqueries
            tailor_pm_sq = TailorOrderSecurityPayment.objects.filter(
                order=OuterRef('pk'), is_paid=True
            ).order_by('-created_at').values('payment_method')[:1]

            tailor_adj_cnt_sq = TailorOrderSecurityDepositAdjustment.objects.filter(
                order=OuterRef('pk')
            ).values('order').annotate(_c=Count('id')).values('_c')[:1]

            tailor_refunded_sq = TailorOrderSecurityDepositAdjustment.objects.filter(
                order=OuterRef('pk'),
                adjustment_type=ADJUSTMENT_TYPES["TAILOR_REFUNDED"]
            ).values('order').annotate(_s=Sum('amount')).values('_s')[:1]

            tailor_deducted_sq = TailorOrderSecurityDepositAdjustment.objects.filter(
                order=OuterRef('pk'),
                adjustment_type=ADJUSTMENT_TYPES["TAILOR_DEDUCTED"]
            ).values('order').annotate(_s=Sum('amount')).values('_s')[:1]

            # Same fix: resolve to unique IDs to avoid JOIN duplication from
            # security_payments__is_paid=True filter.
            tailor_ids = list(overall_tailor_qs.values_list('id', flat=True))
            tailor_breakdown_rows = TailorOrder.objects.filter(id__in=tailor_ids).annotate(
                _pm=Subquery(tailor_pm_sq, output_field=CharField()),
                _adj_cnt=Coalesce(Subquery(tailor_adj_cnt_sq, output_field=IntegerField()), 0),
                _refunded=Coalesce(Subquery(tailor_refunded_sq, output_field=DecimalField()), Decimal('0')),
                _deducted=Coalesce(Subquery(tailor_deducted_sq, output_field=DecimalField()), Decimal('0')),
            ).values('security_amount', 'status', '_pm', '_adj_cnt', '_refunded', '_deducted')

            for row in tailor_breakdown_rows:
                if row['status'] == 'completed' and row['_adj_cnt'] == 0:
                    pending = Decimal('0')
                else:
                    # No per-row clamp — same reasoning as booking loop above.
                    pending = Decimal(str(row['security_amount'])) - row['_refunded'] - row['_deducted']
                raw_method = (row['_pm'] or '').strip().lower()
                bucket = 'cash' if raw_method == 'cash' else 'upi'
                pending_breakdown[bucket] += pending

            # Clamp once after all rows — mirrors the single global clamp on overall_pending_amount.
            total_pending_breakdown = {k: float(max(Decimal('0'), v)) for k, v in pending_breakdown.items()}

            # -------- Pagination --------
            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, content in days.items():
                    flat_group_by_day[f"{month}|{day}"] = content

            paginator = DayGroupedPagination()
            paginator.total_pending_amount = overall_pending_amount
            paginator.total_pending_breakdown = total_pending_breakdown
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # Rebuild paginated structure
            final_group = OrderedDict()
            if isinstance(paginated_group, OrderedDict):
                for key, content in paginated_group.items():
                    month, day = key.split("|")
                    if month not in final_group:
                        final_group[month] = OrderedDict()
                    final_group[month][day] = content
            else:
                final_group = sorted_grouped

            return paginator.get_paginated_response(final_group)

        except Exception as e:
            logger.error(f"PendingSecurityAmountAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching security amounts.",
                dev_message=str(e)
            )


__all__ = ["LedgerSecurityAmountAPIView"]
