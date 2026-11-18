import logging
from collections import defaultdict, OrderedDict
from datetime import datetime
from django.db.models import F, Sum, Value, CharField, DecimalField, Subquery, OuterRef
from django.db.models.functions import Coalesce, Cast
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from ERP.utils.mixins import StandardResponseMixin
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils.shop import get_active_shop_id
from booking_v2.models import (
    BookingV2, Client, PaymentDetailsV2, RefundDetails,
    SecurityDepositAdjustment,
)
from sales.models import SalesPaymentDetails
from tailored.models import CustomerPayment, TailorOrderRefund, TailorOrder
from booking_v2.api.v5.views.pagination import DayGroupedPagination
from booking_v2.utils.ledger import compute_ledger_entries as _compute_ledger_entries
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class PaymentDetailsGroupedAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Booking Management V5"],
        summary="Get grouped payment details",
        description=(
            "Returns grouped payments from booking, sale, and tailor-order modules in Month -> Day format with day totals."
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
            200: OpenApiResponse(description="Grouped day-wise payments response."),
            400: OpenApiResponse(
                description="Validation errors.\n\nExact messages:\n- Client not found.\n- Client not found for this shop."
            ),
            403: OpenApiResponse(
                description="Permission error.\n\nExact message:\n- Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Server error.\n\nExact message:\n- Something went wrong while fetching grouped payment details."
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

            # -------- Validate client --------
            client_id = request.query_params.get("client_id")
            client = None
            if client_id:
                if not client_id.isdigit():
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found."]},
                        message="Client not found."
                    )
                try:
                    client = Client.objects.get(id=client_id, shop_id=shop_id)
                except Client.DoesNotExist:
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found for this shop."]},
                        message="Client not found for this shop."
                    )

            # -------- Booking Payments: Bulk Fetch + Settlement --------
            booking_qs = (
                BookingV2.objects
                .filter(shop_id=shop_id, deleted__isnull=True)
                .exclude(delivery_status="cancelled")
                .values("id", "shop_booking_id", "client_identity__client_name")
            )
            if client:
                booking_qs = booking_qs.filter(client_identity=client)

            booking_ids = []
            booking_meta = {}
            for booking_row in booking_qs.iterator(chunk_size=2000):
                booking_ids.append(booking_row["id"])
                booking_meta[booking_row["id"]] = {
                    "invoice_id": booking_row["shop_booking_id"] or f"B{booking_row['id']}",
                    "client_name": booking_row["client_identity__client_name"],
                }

            raw_booking_payments = (
                PaymentDetailsV2.objects
                .filter(booking_id__in=booking_ids)
                .values("booking_id", "payment_amount", "payment_method", "created_at")
                .order_by("booking_id", "created_at")
            )
            raw_booking_refunds = (
                RefundDetails.objects
                .filter(booking_id__in=booking_ids)
                .values("booking_id", "refunded_amount", "created_at")
                .order_by("booking_id", "created_at")
            )

            booking_payments_map = defaultdict(list)
            for row in raw_booking_payments.iterator(chunk_size=2000):
                booking_payments_map[row["booking_id"]].append(
                    {"amount": row["payment_amount"], "method": row["payment_method"], "created_at": row["created_at"]}
                )

            booking_refunds_map = defaultdict(list)
            for row in raw_booking_refunds.iterator(chunk_size=2000):
                booking_refunds_map[row["booking_id"]].append(
                    {"amount": row["refunded_amount"], "created_at": row["created_at"]}
                )

            booking_ledger_entries = []
            for booking_id in booking_ids:
                meta = booking_meta[booking_id]
                entries = _compute_ledger_entries(
                    payments=sorted(booking_payments_map[booking_id], key=lambda x: x["created_at"]),
                    refunds=sorted(booking_refunds_map[booking_id],   key=lambda x: x["created_at"]),
                    item_id=booking_id,
                    invoice_id=meta["invoice_id"],
                    client_name=meta["client_name"],
                    item_type="booking",
                )
                booking_ledger_entries.extend(entries)

            # -------- Penalty Entries for Cancelled Bookings --------
            cancelled_bookings = (
                BookingV2.objects.filter(
                    shop_id=shop_id,
                    delivery_status="cancelled",
                    deleted__isnull=True
                )
                .annotate(
                    total_refund=Coalesce(
                        Subquery(
                            RefundDetails.objects.filter(booking=OuterRef('pk'))
                            .values('booking')
                            .annotate(total=Sum('refunded_amount'))
                            .values('total')[:1]
                        ),
                        Value(0)
                    ),
                    last_refund_time=Subquery(
                        RefundDetails.objects.filter(booking=OuterRef('pk'))
                        .order_by('-created_at')
                        .values('created_at')[:1]
                    ),
                    sample_method=Subquery(
                        PaymentDetailsV2.objects.filter(booking=OuterRef('pk'))
                        .order_by('-created_at')
                        .values('payment_method')[:1]
                    ),
                )
                .annotate(
                    penalty_amount=Cast(F('advance_amount'), output_field=DecimalField(max_digits=10, decimal_places=2)) -
                                Cast(F('total_refund'), output_field=DecimalField(max_digits=10, decimal_places=2))
                )
                .filter(penalty_amount__gt=0)
                .annotate(
                    item_id=F('id'),
                    invoice_id_field=F('shop_booking_id'),
                    client_name_field=F('client_identity__client_name'),
                    amount_field=F('penalty_amount'),
                    method_field=Coalesce(F('sample_method'), Value('cash', output_field=CharField())),
                    created_at_field=Coalesce(F('last_refund_time'), F('created_at'))
                )
                .values(
                    'item_id',
                    'invoice_id_field',
                    'client_name_field',
                    'amount_field',
                    'method_field',
                    'created_at_field'
                )
            )

            if client:
                cancelled_bookings = cancelled_bookings.filter(client_identity=client)

            # -------- Security Deposit Deductions --------
            # security_deductions = (
            #     SecurityDepositAdjustment.objects.filter(
            #         booking__shop_id=shop_id,
            #         booking__deleted__isnull=True,
            #         adjustment_type="deduction"
            #     )
            #     .select_related("booking", "booking__client_identity")
            #     .annotate(
            #         item_id=F("booking_id"),
            #         invoice_id_field=F("booking__shop_booking_id"),
            #         client_name_field=F("booking__client_identity__client_name"),
            #         amount_field=F("amount"),
            #         method_field=Coalesce(
            #             F("payment_method"),
            #             Value("cash", output_field=CharField())
            #         ),
            #         created_at_field=F("created_at"),
            #     )
            #     .values(
            #         "item_id",
            #         "invoice_id_field",
            #         "client_name_field",
            #         "amount_field",
            #         "method_field",
            #         "created_at_field",
            #     )
            # )

            # if client:
            #     security_deductions = security_deductions.filter(
            #         booking__client_identity=client
            #     )

            # -------- Sales Payments Query --------
            sales_payments = SalesPaymentDetails.objects.filter(
                sale__shop_id=shop_id
            ).select_related("sale").annotate(
                item_id=F('sale_id'),
                invoice_id_field=F('sale__shop_sale_id'),
                client_name_field=F('sale__client__client_name'),
                amount_field=F('payment_amount'),
                method_field=F('payment_method'),
                created_at_field=F('created_at')
            ).values(
                'item_id',
                'invoice_id_field',
                'client_name_field',
                'amount_field',
                'method_field',
                'created_at_field'
            ).order_by('-created_at')

            if client:
                sales_payments = sales_payments.filter(sale__client=client)

            # -------- Tailor Order Payments: Bulk Fetch + Settlement --------
            tailor_qs = (
                TailorOrder.objects
                .filter(shop_id=shop_id, deleted__isnull=True)
                .values("id", "shop_tailor_id", "client__client_name")
            )
            if client:
                tailor_qs = tailor_qs.filter(client=client)

            tailor_ids = []
            tailor_meta = {}
            for tailor_row in tailor_qs.iterator(chunk_size=2000):
                tailor_ids.append(tailor_row["id"])
                tailor_meta[tailor_row["id"]] = {
                    "invoice_id": tailor_row["shop_tailor_id"] or f"TO{tailor_row['id']}",
                    "client_name": tailor_row["client__client_name"],
                }

            raw_tailor_payments = (
                CustomerPayment.objects
                .filter(order_id__in=tailor_ids)
                .values("order_id", "payment_amount", "payment_method", "created_at")
                .order_by("order_id", "created_at")
            )
            raw_tailor_refunds = (
                TailorOrderRefund.objects
                .filter(order_id__in=tailor_ids)
                .values("order_id", "refunded_amount", "created_at")
                .order_by("order_id", "created_at")
            )

            tailor_payments_map = defaultdict(list)
            for row in raw_tailor_payments.iterator(chunk_size=2000):
                tailor_payments_map[row["order_id"]].append(
                    {"amount": row["payment_amount"], "method": row["payment_method"], "created_at": row["created_at"]}
                )

            tailor_refunds_map = defaultdict(list)
            for row in raw_tailor_refunds.iterator(chunk_size=2000):
                tailor_refunds_map[row["order_id"]].append(
                    {"amount": row["refunded_amount"], "created_at": row["created_at"]}
                )

            tailor_ledger_entries = []
            for order_id in tailor_ids:
                meta = tailor_meta[order_id]
                entries = _compute_ledger_entries(
                    payments=sorted(tailor_payments_map[order_id], key=lambda x: x["created_at"]),
                    refunds=sorted(tailor_refunds_map[order_id],   key=lambda x: x["created_at"]),
                    item_id=order_id,
                    invoice_id=meta["invoice_id"],
                    client_name=meta["client_name"],
                    item_type="tailor_order",
                )
                tailor_ledger_entries.extend(entries)

            # -------- Combine All Payments --------
            combined_payments = []

            # Booking ledger entries (pre-formatted by _compute_ledger_entries)
            combined_payments.extend(booking_ledger_entries)

            # Cancelled booking penalties
            for payment in cancelled_bookings.iterator(chunk_size=2000):
                local_created = localtime(payment['created_at_field'])
                combined_payments.append({
                    "id": payment['item_id'],
                    "invoice_id": payment['invoice_id_field'] or f"B{payment['item_id']}",
                    "client_name": payment['client_name_field'],
                    "payment_amount": payment['amount_field'],
                    "payment_method": payment['method_field'],
                    "created_at": local_created.strftime("%d-%m-%Y %H:%M:%S"),
                    "type": "booking",
                    "sort_datetime": local_created
                })

            # # Security deposit deductions
            # for payment in security_deductions.iterator(chunk_size=2000):
            #     local_created = localtime(payment['created_at_field'])
            #     combined_payments.append({
            #         "id": payment['item_id'],
            #         "invoice_id": payment['invoice_id_field'] or f"B{payment['item_id']}",
            #         "client_name": payment['client_name_field'],
            #         "payment_amount": payment['amount_field'],
            #         "payment_method": payment['method_field'],
            #         "created_at": local_created.strftime("%d-%m-%Y %H:%M:%S"),
            #         "type": "security_deduction",
            #         "sort_datetime": local_created
            #     })

            # Sales payments
            for payment in sales_payments.iterator(chunk_size=2000):
                local_created = localtime(payment['created_at_field'])
                combined_payments.append({
                    "id": payment['item_id'],
                    "invoice_id": payment['invoice_id_field'] or f"SL{payment['item_id']}",
                    "client_name": payment['client_name_field'],
                    "payment_amount": payment['amount_field'],
                    "payment_method": payment['method_field'],
                    "created_at": local_created.strftime("%d-%m-%Y %H:%M:%S"),
                    "type": "sale",
                    "sort_datetime": local_created
                })

            # Tailor order ledger entries (pre-formatted by _compute_ledger_entries)
            combined_payments.extend(tailor_ledger_entries)

            # -------- Sort All Payments by Date (Newest First) --------
            combined_payments.sort(key=lambda x: x['sort_datetime'], reverse=True)

            # -------- Group by Date --------
            grouped_by_date = defaultdict(list)
            for payment in combined_payments:
                date_key = payment['sort_datetime'].strftime("%d-%m-%Y")
                month_key = payment['sort_datetime'].strftime("%B %Y")

                # Remove sort helper field for final output
                final_payment = {k: v for k, v in payment.items() if k != 'sort_datetime'}
                grouped_by_date[(month_key, date_key)].append(final_payment)

            # -------- Sort Keys (Month/Day) --------
            def get_date_tuple(item):
                _, day = item
                return datetime.strptime(day, "%d-%m-%Y")

            sorted_dates = sorted(
                grouped_by_date.keys(),
                key=get_date_tuple,
                reverse=True
            )

            # -------- Build Month - Day - {payments, items} --------
            sorted_grouped = OrderedDict()
            for month, day in sorted_dates:
                if month not in sorted_grouped:
                    sorted_grouped[month] = OrderedDict()

                day_items = sorted(
                    grouped_by_date[(month, day)],
                    key=lambda x: datetime.strptime(x['created_at'], "%d-%m-%Y %H:%M:%S"),
                    reverse=True
                )

                # -------- Daily Payment Summary --------
                method_mapping = {
                    'gpay': 'upi',
                    'cash': 'cash',
                    'upi': 'upi'
                }

                upi_total = sum(
                    item["payment_amount"] for item in day_items
                    if method_mapping.get(item["payment_method"].lower(), item["payment_method"].lower()) == "upi"
                )
                cash_total = sum(
                    item["payment_amount"] for item in day_items
                    if method_mapping.get(item["payment_method"].lower(), item["payment_method"].lower()) == "cash"
                )
                total = sum(item["payment_amount"] for item in day_items)

                sorted_grouped[month][day] = {
                    "payments": {
                        "upi": upi_total,
                        "cash": cash_total,
                        "total": total
                    },
                    "items": day_items
                }

            # -------- Pagination --------
            flat_group_by_day = OrderedDict()
            for month, days in sorted_grouped.items():
                for day, content in days.items():
                    flat_group_by_day[f"{month}|{day}"] = content

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # -------- Rebuild Paginated Structure --------
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
            logger.error(f"PaymentDetailsGroupedAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching grouped payment details.",
                dev_message=str(e)
            )


__all__ = ["PaymentDetailsGroupedAPIView"]
