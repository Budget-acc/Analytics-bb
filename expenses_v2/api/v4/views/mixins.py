import logging
from collections import defaultdict, namedtuple
from datetime import datetime

from django.db import models
from django.db.models import F, Q, Value, CharField, DecimalField, Subquery, OuterRef, Exists, Sum
from django.db.models.functions import Coalesce, Cast
from django.utils.timezone import localtime, make_aware

from booking_v2.models import BookingV2, PaymentDetailsV2, Client, RefundDetails
from expenses_v2.models import Expenses, ProductVariantExpense
from ERP.utils.mixins import StandardResponseMixin
from sales.models import Sale, SalesPaymentDetails
from shop.models import ShopUser
from shop.api.v3.utils.shop import get_active_shop_id
from tailored.models import TailorOrder, CustomerPayment, TailorOrderRefund

logger = logging.getLogger("api_requests")


def _compute_ledger_entries(payments, refunds):
    """
    Ledger settlement logic — matches PaymentDetailsGroupedAPIView exactly.
    See docs/ledger_payment_settlement_logic.md for full algorithm and examples.

    payments: [{'amount': int/Decimal, 'method': str, 'created_at': datetime}, ...]  sorted ASC
    refunds:  [{'amount': Decimal, 'created_at': datetime}, ...]                      sorted ASC
    """
    if not payments:
        return []

    if not refunds:
        return [
            {"amount": p["amount"], "method": p["method"], "created_at": p["created_at"]}
            for p in payments
        ]

    latest_refund_time = refunds[-1]["created_at"]
    latest_payment_time = payments[-1]["created_at"]
    total_refunds = sum(r["amount"] for r in refunds)

    if latest_refund_time >= latest_payment_time:
        total_payments = sum(p["amount"] for p in payments)
        net = total_payments - total_refunds
        if net > 0:
            return [{"amount": net, "method": payments[-1]["method"], "created_at": latest_refund_time}]
        return []

    payments_before = [p for p in payments if p["created_at"] <= latest_refund_time]
    payments_after  = [p for p in payments if p["created_at"] >  latest_refund_time]

    entries = []
    if payments_before:
        net = sum(p["amount"] for p in payments_before) - total_refunds
        if net > 0:
            entries.append({
                "amount": net,
                "method": payments_before[-1]["method"],
                "created_at": latest_refund_time,
            })

    for p in payments_after:
        entries.append({"amount": p["amount"], "method": p["method"], "created_at": p["created_at"]})

    return entries


class ShopTransactionExportMixin(StandardResponseMixin):
    def parse_date(self, date_str):
        if not date_str or not isinstance(date_str, str):
            return None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    def get_transaction_data(self, request):
        shop_id = get_active_shop_id(request)
        from_date = self.parse_date(request.query_params.get("from_date"))
        to_date = self.parse_date(request.query_params.get("to_date"))
        txn_types = request.query_params.get("type", "all").split(",")
        txn_types = [t.strip().lower() for t in txn_types]

        client_id = request.query_params.get("client_id")

        if not from_date or not to_date:
            raise ValueError("Dates are required.")

        if from_date > to_date:
            raise ValueError("Invalid date range: Start date cannot be later than end date.")

        if client_id:
            exists = Client.objects.filter(id=client_id, shop_id=shop_id).exists()
            if not exists:
                raise ValueError("Client not found.")

        transactions = self.get_transactions(shop_id, from_date, to_date, txn_types, client_id)
        return transactions

    def get_transactions(self, shop_id, from_date, to_date, txn_types, client_id=None):
        Txn = namedtuple("Txn", ["sort_date", "data"])
        transactions = []
        pending_transactions = []

        # ---------- EXPENSES ----------
        if "all" in txn_types or "expense" in txn_types:
            expense_date_filter = (
                Q(expense_date_time__date__range=(from_date, to_date)) |
                Q(expense_date_time__isnull=True, date__range=(from_date, to_date))
            )

            general_expenses = Expenses.objects.filter(
                shop_id=shop_id
            ).filter(expense_date_filter).select_related("user").annotate(
                name_field=F('user__full_name'),
                description_field=Coalesce(F('type'), Value('General expense'))
            ).values(
                'date', 'expense_date_time', 'name_field', 'description_field',
                'expense', 'created_at', 'payment_method'
            )

            # Get owner name for fallback
            owner_user = (
                ShopUser.objects
                .filter(shop_id=shop_id, role="OWNER", is_active=True)
                .select_related("user")
                .first()
            )
            owner_name = owner_user.user.full_name if owner_user and owner_user.user else None

            for exp in general_expenses:
                expense_sort_date = (
                    exp['expense_date_time'] if exp['expense_date_time']
                    else make_aware(datetime.combine(exp['date'], datetime.min.time())) if exp['date']
                    else exp['created_at']
                )
                display_date = (
                    localtime(exp['expense_date_time']).strftime("%d-%m-%Y")
                    if exp['expense_date_time'] else exp['date'].strftime("%d-%m-%Y")
                )

                payment_method = exp.get('payment_method', 'cash') or 'cash'
                payment_text = "Paid via UPI" if payment_method.lower() == 'upi' else "Paid by Cash"
                base_description = exp['description_field'] or "Expense"
                full_description = f"{base_description} ({payment_text})"

                transactions.append(Txn(
                    sort_date=expense_sort_date,
                    data={
                        "type": "expense",
                        "date": display_date,
                        "name": exp['name_field'] if exp['name_field'] else owner_name,
                        "description": full_description,
                        "amount": exp['expense'],
                    }
                ))

            variant_expenses = ProductVariantExpense.objects.filter(
                shop_id=shop_id
            ).filter(expense_date_filter).select_related("linked_expense__user", "product_variant__product").annotate(
                name_field=F('linked_expense__user__full_name'),
                product_name_field=F('product_variant__product__name'),
                description_field=Coalesce(F('type'), Value('Product variant expense'))
            ).values(
                'date', 'expense_date_time', 'name_field', 'description_field',
                'expense', 'created_at', 'product_name_field'
            )

            for exp in variant_expenses:
                user_name = exp['name_field'] if exp['name_field'] else owner_name
                product_name = exp['product_name_field'] or ""
                description = exp['description_field'] or f"Product variant expense - {product_name}"
                expense_sort_date = (
                    exp['expense_date_time'] if exp['expense_date_time']
                    else make_aware(datetime.combine(exp['date'], datetime.min.time())) if exp['date']
                    else exp['created_at']
                )
                display_date = (
                    localtime(exp['expense_date_time']).strftime("%d-%m-%Y")
                    if exp['expense_date_time'] else exp['date'].strftime("%d-%m-%Y")
                )

                payment_method = exp.get('payment_method', 'cash') or 'cash'
                payment_text = "Paid via UPI" if payment_method.lower() == 'upi' else "Paid by Cash"
                full_description = f"{description} ({payment_text})"

                transactions.append(Txn(
                    sort_date=expense_sort_date,
                    data={
                        "type": "expense",
                        "date": display_date,
                        "name": user_name,
                        "description": full_description,
                        "amount": exp['expense'],
                    }
                ))

        # ---------- PAYMENTS ----------
        if "all" in txn_types or "payments" in txn_types:
            # =================================================================
            # OLD PAYMENT SECTION — preserved for rollback reference
            # =================================================================
            # # Booking payments (exclude cancelled bookings)
            # booking_payments = PaymentDetailsV2.objects.filter(
            #     booking__shop_id=shop_id,
            #     booking__deleted__isnull=True,
            #     created_at__date__range=(from_date, to_date)
            # ).exclude(
            #     booking__delivery_status="cancelled"
            # ).select_related("booking__client_identity").annotate(
            #     client_name_field=F('booking__client_identity__client_name'),
            #     invoice_id_field=F('booking__shop_booking_id'),
            #     method_field=F('payment_method'),
            #     booking_id_field=F('booking_id')
            # ).values(
            #     'client_name_field', 'invoice_id_field', 'booking_id_field',
            #     'payment_amount', 'method_field', 'created_at'
            # )
            #
            # if client_id:
            #     booking_payments = booking_payments.filter(booking__client_identity_id=client_id)
            #
            # for pay in booking_payments:
            #     local_dt = localtime(pay['created_at'])
            #     raw_method = (pay['method_field'] or '').lower().strip()
            #     normalized_method = 'upi' if raw_method in ['upi', 'gpay'] else 'cash'
            #     client_name = pay['client_name_field']
            #     name = client_name if client_name else (pay['invoice_id_field'] or f"B{pay['booking_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Payment via {normalized_method.upper()}",
            #             "amount": pay['payment_amount'],
            #         }
            #     ))
            #
            # # Penalty entries for cancelled bookings
            # cancelled_booking_penalties = (
            #     BookingV2.objects.filter(
            #         shop_id=shop_id, delivery_status="cancelled", deleted__isnull=True
            #     )
            #     .annotate(
            #         total_refund=Coalesce(
            #             Subquery(
            #                 RefundDetails.objects.filter(booking=OuterRef('pk'))
            #                 .values('booking').annotate(total=models.Sum('refunded_amount')).values('total')[:1]
            #             ),
            #             Value(0, output_field=DecimalField(max_digits=10, decimal_places=2))
            #         ),
            #         last_refund_time=Subquery(
            #             RefundDetails.objects.filter(booking=OuterRef('pk'))
            #             .order_by('-created_at').values('created_at')[:1]
            #         ),
            #         sample_method=Subquery(
            #             PaymentDetailsV2.objects.filter(booking=OuterRef('pk'))
            #             .order_by('-created_at').values('payment_method')[:1]
            #         )
            #     )
            #     .annotate(
            #         penalty_amount=Cast(F('advance_amount'), output_field=DecimalField(max_digits=10, decimal_places=2)) -
            #                     Cast(F('total_refund'), output_field=DecimalField(max_digits=10, decimal_places=2))
            #     )
            #     .filter(penalty_amount__gt=0)
            #     .filter(
            #         Q(last_refund_time__date__range=(from_date, to_date)) |
            #         (Q(last_refund_time__isnull=True) & Q(created_at__date__range=(from_date, to_date)))
            #     )
            #     .select_related("client_identity")
            #     .annotate(
            #         client_name_field=F('client_identity__client_name'),
            #         invoice_id_field=F('shop_booking_id'),
            #         booking_id_field=F('id'),
            #         method_field=Coalesce(F('sample_method'), Value('cash', output_field=CharField())),
            #         created_at_field=Coalesce(F('last_refund_time'), F('created_at'))
            #     )
            #     .values(
            #         'client_name_field', 'invoice_id_field', 'booking_id_field',
            #         'penalty_amount', 'method_field', 'created_at_field'
            #     )
            # )
            # if client_id:
            #     cancelled_booking_penalties = cancelled_booking_penalties.filter(client_identity_id=client_id)
            # for penalty in cancelled_booking_penalties:
            #     local_dt = localtime(penalty['created_at_field'])
            #     raw_method = (penalty['method_field'] or '').lower().strip()
            #     normalized_method = 'upi' if raw_method in ['upi', 'gpay'] else 'cash'
            #     client_name = penalty['client_name_field']
            #     name = client_name if client_name else (penalty['invoice_id_field'] or f"B{penalty['booking_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Penalty via {normalized_method.upper()}",
            #             "amount": float(penalty['penalty_amount']),
            #         }
            #     ))
            #
            # # Sales payments
            # sales_payments = SalesPaymentDetails.objects.filter(
            #     sale__shop_id=shop_id,
            #     created_at__date__range=(from_date, to_date)
            # ).select_related("sale__client").annotate(
            #     client_name_field=F('sale__client__client_name'),
            #     invoice_id_field=F('sale__shop_sale_id'),
            #     method_field=F('payment_method'),
            #     sale_id_field=F('sale_id')
            # ).values(
            #     'client_name_field', 'invoice_id_field', 'sale_id_field',
            #     'payment_amount', 'method_field', 'created_at'
            # )
            # if client_id:
            #     sales_payments = sales_payments.filter(sale__client_id=client_id)
            # for pay in sales_payments:
            #     local_dt = localtime(pay['created_at'])
            #     raw_method = (pay['method_field'] or '').lower().strip()
            #     normalized_method = 'upi' if raw_method in ['upi', 'gpay'] else 'cash'
            #     client_name = pay['client_name_field']
            #     name = client_name if client_name else (pay['invoice_id_field'] or f"SL{pay['sale_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Payment via {normalized_method.upper()}",
            #             "amount": pay['payment_amount'],
            #         }
            #     ))
            #
            # # Tailor order payments
            # tailor_payments = CustomerPayment.objects.filter(
            #     order__shop_id=shop_id,
            #     order__deleted__isnull=True,
            #     created_at__date__range=(from_date, to_date)
            # ).select_related("order__client").annotate(
            #     client_name_field=F('order__client__client_name'),
            #     invoice_id_field=F('order__shop_tailor_id'),
            #     method_field=F('payment_method'),
            #     order_id_field=F('order_id')
            # ).values(
            #     'client_name_field', 'invoice_id_field', 'order_id_field',
            #     'payment_amount', 'method_field', 'created_at'
            # )
            # if client_id:
            #     tailor_payments = tailor_payments.filter(order__client_id=client_id)
            # for pay in tailor_payments:
            #     local_dt = localtime(pay['created_at'])
            #     raw_method = (pay['method_field'] or '').lower().strip()
            #     normalized_method = 'upi' if raw_method in ['upi', 'gpay'] else 'cash'
            #     client_name = pay['client_name_field']
            #     name = client_name if client_name else (pay['invoice_id_field'] or f"TO{pay['order_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Payment via {normalized_method.upper()}",
            #             "amount": pay['payment_amount'],
            #         }
            #     ))

            # =================================================================
            # NEW PAYMENT SECTION — refund-aware, mirrors PaymentDetailsGroupedAPIView
            # Bookings/tailor orders with refunds → single net entry (payments − refunds)
            # dated at latest refund; orders with no refunds → raw payment rows.
            # Date filtering: payment rows by created_at; net entries by last_refund_time.
            # =================================================================

            # =================================================================
            # OLD Queries A + B — preserved for rollback reference
            # =================================================================
            # # ---- A (OLD): Normal booking payments (bookings with NO refunds) ----
            # _booking_has_refund = Exists(
            #     RefundDetails.objects.filter(booking_id=OuterRef("booking_id"))
            # )
            # booking_payments = (
            #     PaymentDetailsV2.objects
            #     .filter(
            #         booking__shop_id=shop_id,
            #         booking__deleted__isnull=True,
            #         created_at__date__range=(from_date, to_date),
            #     )
            #     .exclude(booking__delivery_status="cancelled")
            #     .annotate(_has_refund=_booking_has_refund)
            #     .filter(_has_refund=False)
            #     .annotate(
            #         client_name_field=F("booking__client_identity__client_name"),
            #         invoice_id_field=F("booking__shop_booking_id"),
            #         method_field=F("payment_method"),
            #         booking_id_field=F("booking_id"),
            #     )
            #     .values(
            #         "client_name_field", "invoice_id_field", "booking_id_field",
            #         "payment_amount", "method_field", "created_at",
            #     )
            # )
            # if client_id:
            #     booking_payments = booking_payments.filter(booking__client_identity_id=client_id)
            # for pay in booking_payments:
            #     local_dt = localtime(pay["created_at"])
            #     raw_method = (pay["method_field"] or "").lower().strip()
            #     normalized_method = "upi" if raw_method in ["upi", "gpay"] else "cash"
            #     client_name = pay["client_name_field"]
            #     name = client_name if client_name else (pay["invoice_id_field"] or f"B{pay['booking_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Payment via {normalized_method.upper()}",
            #             "amount": pay["payment_amount"],
            #         }
            #     ))
            #
            # # ---- B (OLD): Bookings WITH refunds → single net entry, dated at latest refund ----
            # _booking_has_refund_q = Exists(
            #     RefundDetails.objects.filter(booking_id=OuterRef("pk"))
            # )
            # bookings_with_refunds = (
            #     BookingV2.objects
            #     .filter(shop_id=shop_id, deleted__isnull=True)
            #     .exclude(delivery_status="cancelled")
            #     .annotate(_has_refund=_booking_has_refund_q)
            #     .filter(_has_refund=True)
            #     .annotate(
            #         total_payments=Coalesce(
            #             Subquery(
            #                 PaymentDetailsV2.objects.filter(booking=OuterRef("pk"))
            #                 .values("booking")
            #                 .annotate(t=Sum("payment_amount", output_field=DecimalField()))
            #                 .values("t")[:1]
            #             ),
            #             Value(0, output_field=DecimalField()),
            #         ),
            #         total_refunds=Coalesce(
            #             Subquery(
            #                 RefundDetails.objects.filter(booking=OuterRef("pk"))
            #                 .values("booking")
            #                 .annotate(t=Sum("refunded_amount", output_field=DecimalField()))
            #                 .values("t")[:1]
            #             ),
            #             Value(0, output_field=DecimalField()),
            #         ),
            #         last_refund_time=Subquery(
            #             RefundDetails.objects.filter(booking=OuterRef("pk"))
            #             .order_by("-created_at").values("created_at")[:1]
            #         ),
            #         sample_method=Subquery(
            #             PaymentDetailsV2.objects.filter(booking=OuterRef("pk"))
            #             .order_by("-created_at").values("payment_method")[:1]
            #         ),
            #     )
            #     .annotate(
            #         net_amount=Cast(F("total_payments"), output_field=DecimalField(max_digits=12, decimal_places=2))
            #                  - Cast(F("total_refunds"),  output_field=DecimalField(max_digits=12, decimal_places=2))
            #     )
            #     .filter(net_amount__gt=0)
            #     .filter(last_refund_time__date__range=(from_date, to_date))
            #     .annotate(
            #         client_name_field=F("client_identity__client_name"),
            #         invoice_id_field=F("shop_booking_id"),
            #         booking_id_field=F("id"),
            #         method_field=Coalesce(F("sample_method"), Value("cash", output_field=CharField())),
            #         created_at_field=Coalesce(F("last_refund_time"), F("created_at")),
            #     )
            #     .values(
            #         "client_name_field", "invoice_id_field", "booking_id_field",
            #         "net_amount", "method_field", "created_at_field",
            #     )
            # )
            # if client_id:
            #     bookings_with_refunds = bookings_with_refunds.filter(client_identity_id=client_id)
            # for entry in bookings_with_refunds:
            #     local_dt = localtime(entry["created_at_field"])
            #     raw_method = (entry["method_field"] or "").lower().strip()
            #     normalized_method = "upi" if raw_method in ["upi", "gpay"] else "cash"
            #     client_name = entry["client_name_field"]
            #     name = client_name if client_name else (entry["invoice_id_field"] or f"B{entry['booking_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Payment via {normalized_method.upper()}",
            #             "amount": float(entry["net_amount"]),
            #         }
            #     ))

            # ---- A+B (NEW): Booking payments — ledger settlement (matches _compute_ledger_entries) ----
            # Step 1: find candidate booking IDs — any booking with payment OR refund in date range.
            # Step 2: load ALL their history (no date filter), process with _compute_ledger_entries.
            # Step 3: filter output entries to the date range.
            _bp_id_qs = (
                PaymentDetailsV2.objects
                .filter(
                    booking__shop_id=shop_id,
                    booking__deleted__isnull=True,
                    created_at__date__range=(from_date, to_date),
                )
                .exclude(booking__delivery_status="cancelled")
            )
            _br_id_qs = (
                RefundDetails.objects
                .filter(
                    booking__shop_id=shop_id,
                    booking__deleted__isnull=True,
                    created_at__date__range=(from_date, to_date),
                )
                .exclude(booking__delivery_status="cancelled")
            )
            if client_id:
                _bp_id_qs = _bp_id_qs.filter(booking__client_identity_id=client_id)
                _br_id_qs = _br_id_qs.filter(booking__client_identity_id=client_id)

            _candidate_booking_ids = (
                set(_bp_id_qs.values_list("booking_id", flat=True)) |
                set(_br_id_qs.values_list("booking_id", flat=True))
            )

            if _candidate_booking_ids:
                _all_bp_qs = (
                    PaymentDetailsV2.objects
                    .filter(booking_id__in=_candidate_booking_ids)
                    .values(
                        "booking_id", "payment_amount", "payment_method", "created_at",
                        "booking__shop_booking_id", "booking__client_identity__client_name",
                    )
                    .order_by("created_at")
                )
                _all_br_qs = (
                    RefundDetails.objects
                    .filter(booking_id__in=_candidate_booking_ids)
                    .values("booking_id", "refunded_amount", "created_at")
                    .order_by("created_at")
                )

                _payments_by_booking = defaultdict(list)
                _booking_meta = {}
                for row in _all_bp_qs:
                    bid = row["booking_id"]
                    _payments_by_booking[bid].append({
                        "amount": row["payment_amount"],
                        "method": row["payment_method"],
                        "created_at": row["created_at"],
                    })
                    if bid not in _booking_meta:
                        _booking_meta[bid] = {
                            "invoice_id": row["booking__shop_booking_id"],
                            "client_name": row["booking__client_identity__client_name"],
                        }

                _refunds_by_booking = defaultdict(list)
                for row in _all_br_qs:
                    _refunds_by_booking[row["booking_id"]].append({
                        "amount": row["refunded_amount"],
                        "created_at": row["created_at"],
                    })

                for bid in _candidate_booking_ids:
                    meta = _booking_meta.get(bid, {"invoice_id": None, "client_name": None})
                    entries = _compute_ledger_entries(
                        _payments_by_booking.get(bid, []),
                        _refunds_by_booking.get(bid, []),
                    )
                    for entry in entries:
                        local_dt = localtime(entry["created_at"])
                        if not (from_date <= local_dt.date() <= to_date):
                            continue
                        raw_method = (entry["method"] or "").lower().strip()
                        normalized_method = "upi" if raw_method in ["upi", "gpay"] else "cash"
                        client_name = meta["client_name"]
                        name = client_name if client_name else (meta["invoice_id"] or f"B{bid}")
                        transactions.append(Txn(
                            sort_date=local_dt,
                            data={
                                "type": "payment",
                                "date": local_dt.strftime("%d-%m-%Y"),
                                "name": name,
                                "description": f"Payment via {normalized_method.upper()}",
                                "amount": entry["amount"],
                            }
                        ))

            # ---- C: Cancelled booking penalties (unchanged logic) ----
            cancelled_booking_penalties = (
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
                        Value(0, output_field=DecimalField(max_digits=10, decimal_places=2))
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
                    )
                )
                .annotate(
                    penalty_amount=Cast(F('advance_amount'), output_field=DecimalField(max_digits=10, decimal_places=2)) -
                                Cast(F('total_refund'), output_field=DecimalField(max_digits=10, decimal_places=2))
                )
                .filter(penalty_amount__gt=0)
                .filter(
                    Q(last_refund_time__date__range=(from_date, to_date)) |
                    (Q(last_refund_time__isnull=True) & Q(created_at__date__range=(from_date, to_date)))
                )
                .select_related("client_identity")
                .annotate(
                    client_name_field=F('client_identity__client_name'),
                    invoice_id_field=F('shop_booking_id'),
                    booking_id_field=F('id'),
                    method_field=Coalesce(
                        F('sample_method'),
                        Value('cash', output_field=CharField())
                    ),
                    created_at_field=Coalesce(F('last_refund_time'), F('created_at'))
                )
                .values(
                    'client_name_field', 'invoice_id_field', 'booking_id_field',
                    'penalty_amount', 'method_field', 'created_at_field'
                )
            )
            if client_id:
                cancelled_booking_penalties = cancelled_booking_penalties.filter(client_identity_id=client_id)

            for penalty in cancelled_booking_penalties:
                local_dt = localtime(penalty['created_at_field'])
                raw_method = (penalty['method_field'] or '').lower().strip()
                normalized_method = 'upi' if raw_method in ['upi', 'gpay'] else 'cash'
                client_name = penalty['client_name_field']
                name = client_name if client_name else (penalty['invoice_id_field'] or f"B{penalty['booking_id_field']}")
                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "payment",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": f"Penalty via {normalized_method.upper()}",
                        "amount": float(penalty['penalty_amount']),
                    }
                ))

            # ---- D: Sales payments (unchanged) ----
            sales_payments = SalesPaymentDetails.objects.filter(
                sale__shop_id=shop_id,
                created_at__date__range=(from_date, to_date)
            ).select_related("sale__client").annotate(
                client_name_field=F('sale__client__client_name'),
                invoice_id_field=F('sale__shop_sale_id'),
                method_field=F('payment_method'),
                sale_id_field=F('sale_id')
            ).values(
                'client_name_field', 'invoice_id_field', 'sale_id_field',
                'payment_amount', 'method_field', 'created_at'
            )
            if client_id:
                sales_payments = sales_payments.filter(sale__client_id=client_id)

            for pay in sales_payments:
                local_dt = localtime(pay['created_at'])
                raw_method = (pay['method_field'] or '').lower().strip()
                normalized_method = 'upi' if raw_method in ['upi', 'gpay'] else 'cash'
                client_name = pay['client_name_field']
                name = client_name if client_name else (pay['invoice_id_field'] or f"SL{pay['sale_id_field']}")
                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "payment",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": f"Payment via {normalized_method.upper()}",
                        "amount": pay['payment_amount'],
                    }
                ))

            # =================================================================
            # OLD Queries E + F — preserved for rollback reference
            # =================================================================
            # # ---- E (OLD): Normal tailor payments (orders with NO refunds) ----
            # _tailor_has_refund = Exists(
            #     TailorOrderRefund.objects.filter(order_id=OuterRef("order_id"))
            # )
            # tailor_payments = (
            #     CustomerPayment.objects
            #     .filter(
            #         order__shop_id=shop_id,
            #         order__deleted__isnull=True,
            #         created_at__date__range=(from_date, to_date),
            #     )
            #     .annotate(_has_refund=_tailor_has_refund)
            #     .filter(_has_refund=False)
            #     .annotate(
            #         client_name_field=F("order__client__client_name"),
            #         invoice_id_field=F("order__shop_tailor_id"),
            #         method_field=F("payment_method"),
            #         order_id_field=F("order_id"),
            #     )
            #     .values(
            #         "client_name_field", "invoice_id_field", "order_id_field",
            #         "payment_amount", "method_field", "created_at",
            #     )
            # )
            # if client_id:
            #     tailor_payments = tailor_payments.filter(order__client_id=client_id)
            # for pay in tailor_payments:
            #     local_dt = localtime(pay["created_at"])
            #     raw_method = (pay["method_field"] or "").lower().strip()
            #     normalized_method = "upi" if raw_method in ["upi", "gpay"] else "cash"
            #     client_name = pay["client_name_field"]
            #     name = client_name if client_name else (pay["invoice_id_field"] or f"TO{pay['order_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Payment via {normalized_method.upper()}",
            #             "amount": pay["payment_amount"],
            #         }
            #     ))
            #
            # # ---- F (OLD): Tailor orders WITH refunds → single net entry, dated at latest refund ----
            # _tailor_has_refund_o = Exists(
            #     TailorOrderRefund.objects.filter(order_id=OuterRef("pk"))
            # )
            # tailor_orders_with_refunds = (
            #     TailorOrder.objects
            #     .filter(shop_id=shop_id, deleted__isnull=True)
            #     .annotate(_has_refund=_tailor_has_refund_o)
            #     .filter(_has_refund=True)
            #     .annotate(
            #         total_payments=Coalesce(
            #             Subquery(
            #                 CustomerPayment.objects.filter(order=OuterRef("pk"))
            #                 .values("order")
            #                 .annotate(t=Sum("payment_amount", output_field=DecimalField()))
            #                 .values("t")[:1]
            #             ),
            #             Value(0, output_field=DecimalField()),
            #         ),
            #         total_refunds=Coalesce(
            #             Subquery(
            #                 TailorOrderRefund.objects.filter(order=OuterRef("pk"))
            #                 .values("order")
            #                 .annotate(t=Sum("refunded_amount", output_field=DecimalField()))
            #                 .values("t")[:1]
            #             ),
            #             Value(0, output_field=DecimalField()),
            #         ),
            #         last_refund_time=Subquery(
            #             TailorOrderRefund.objects.filter(order=OuterRef("pk"))
            #             .order_by("-created_at").values("created_at")[:1]
            #         ),
            #         sample_method=Subquery(
            #             CustomerPayment.objects.filter(order=OuterRef("pk"))
            #             .order_by("-created_at").values("payment_method")[:1]
            #         ),
            #     )
            #     .annotate(
            #         net_amount=Cast(F("total_payments"), output_field=DecimalField(max_digits=12, decimal_places=2))
            #                  - Cast(F("total_refunds"),  output_field=DecimalField(max_digits=12, decimal_places=2))
            #     )
            #     .filter(net_amount__gt=0)
            #     .filter(last_refund_time__date__range=(from_date, to_date))
            #     .annotate(
            #         client_name_field=F("client__client_name"),
            #         invoice_id_field=F("shop_tailor_id"),
            #         order_id_field=F("id"),
            #         method_field=Coalesce(F("sample_method"), Value("cash", output_field=CharField())),
            #         created_at_field=Coalesce(F("last_refund_time"), F("created_at")),
            #     )
            #     .values(
            #         "client_name_field", "invoice_id_field", "order_id_field",
            #         "net_amount", "method_field", "created_at_field",
            #     )
            # )
            # if client_id:
            #     tailor_orders_with_refunds = tailor_orders_with_refunds.filter(client_id=client_id)
            # for entry in tailor_orders_with_refunds:
            #     local_dt = localtime(entry["created_at_field"])
            #     raw_method = (entry["method_field"] or "").lower().strip()
            #     normalized_method = "upi" if raw_method in ["upi", "gpay"] else "cash"
            #     client_name = entry["client_name_field"]
            #     name = client_name if client_name else (entry["invoice_id_field"] or f"TO{entry['order_id_field']}")
            #     transactions.append(Txn(
            #         sort_date=local_dt,
            #         data={
            #             "type": "payment",
            #             "date": local_dt.strftime("%d-%m-%Y"),
            #             "name": name,
            #             "description": f"Payment via {normalized_method.upper()}",
            #             "amount": float(entry["net_amount"]),
            #         }
            #     ))

            # ---- E+F (NEW): Tailor payments — ledger settlement (matches _compute_ledger_entries) ----
            _tp_id_qs = (
                CustomerPayment.objects
                .filter(
                    order__shop_id=shop_id,
                    order__deleted__isnull=True,
                    created_at__date__range=(from_date, to_date),
                )
            )
            _tr_id_qs = (
                TailorOrderRefund.objects
                .filter(
                    order__shop_id=shop_id,
                    order__deleted__isnull=True,
                    created_at__date__range=(from_date, to_date),
                )
            )
            if client_id:
                _tp_id_qs = _tp_id_qs.filter(order__client_id=client_id)
                _tr_id_qs = _tr_id_qs.filter(order__client_id=client_id)

            _candidate_tailor_ids = (
                set(_tp_id_qs.values_list("order_id", flat=True)) |
                set(_tr_id_qs.values_list("order_id", flat=True))
            )

            if _candidate_tailor_ids:
                _all_tp_qs = (
                    CustomerPayment.objects
                    .filter(order_id__in=_candidate_tailor_ids)
                    .values(
                        "order_id", "payment_amount", "payment_method", "created_at",
                        "order__shop_tailor_id", "order__client__client_name",
                    )
                    .order_by("created_at")
                )
                _all_tr_qs = (
                    TailorOrderRefund.objects
                    .filter(order_id__in=_candidate_tailor_ids)
                    .values("order_id", "refunded_amount", "created_at")
                    .order_by("created_at")
                )

                _payments_by_tailor = defaultdict(list)
                _tailor_meta = {}
                for row in _all_tp_qs:
                    oid = row["order_id"]
                    _payments_by_tailor[oid].append({
                        "amount": row["payment_amount"],
                        "method": row["payment_method"],
                        "created_at": row["created_at"],
                    })
                    if oid not in _tailor_meta:
                        _tailor_meta[oid] = {
                            "invoice_id": row["order__shop_tailor_id"],
                            "client_name": row["order__client__client_name"],
                        }

                _refunds_by_tailor = defaultdict(list)
                for row in _all_tr_qs:
                    _refunds_by_tailor[row["order_id"]].append({
                        "amount": row["refunded_amount"],
                        "created_at": row["created_at"],
                    })

                for oid in _candidate_tailor_ids:
                    meta = _tailor_meta.get(oid, {"invoice_id": None, "client_name": None})
                    entries = _compute_ledger_entries(
                        _payments_by_tailor.get(oid, []),
                        _refunds_by_tailor.get(oid, []),
                    )
                    for entry in entries:
                        local_dt = localtime(entry["created_at"])
                        if not (from_date <= local_dt.date() <= to_date):
                            continue
                        raw_method = (entry["method"] or "").lower().strip()
                        normalized_method = "upi" if raw_method in ["upi", "gpay"] else "cash"
                        client_name = meta["client_name"]
                        name = client_name if client_name else (meta["invoice_id"] or f"TO{oid}")
                        transactions.append(Txn(
                            sort_date=local_dt,
                            data={
                                "type": "payment",
                                "date": local_dt.strftime("%d-%m-%Y"),
                                "name": name,
                                "description": f"Payment via {normalized_method.upper()}",
                                "amount": entry["amount"],
                            }
                        ))

        # ---------- SECURITY TRANSACTIONS ----------
        if "all" in txn_types or "security" in txn_types:
            # Booking security
            booking_security = BookingV2.objects.filter(
                shop_id=shop_id,
                booking_status="upcoming",
                created_at__date__range=(from_date, to_date),
                security_amount__gt=0,
                deleted__isnull=True
            ).exclude(delivery_status="cancelled"
            ).select_related("client_identity").annotate(
                client_name_field=F('client_identity__client_name'),
                invoice_id_field=F('shop_booking_id'),
                booking_id_field=F('id')
            ).values(
                'client_name_field', 'invoice_id_field', 'booking_id_field',
                'security_amount', 'created_at'
            )

            if client_id:
                booking_security = booking_security.filter(client_identity_id=client_id)

            for booking in booking_security:
                local_dt = localtime(booking['created_at'])

                # Use client name if available, else fallback to invoice ID
                client_name = booking['client_name_field']
                name = client_name if client_name else (booking['invoice_id_field'] or f"B{booking['booking_id_field']}")

                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "security",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": "Security collected",
                        "amount": booking['security_amount'],
                    }
                ))

            # Tailor order security
            tailor_security = TailorOrder.objects.filter(
                shop_id=shop_id,
                created_at__date__range=(from_date, to_date),
                security_amount__gt=0,
                deleted__isnull=True,
                created_booking__isnull=True
            ).select_related("client").annotate(
                client_name_field=F('client__client_name'),
                invoice_id_field=F('shop_tailor_id'),
                order_id_field=F('id')
            ).values(
                'client_name_field', 'invoice_id_field', 'order_id_field',
                'security_amount', 'created_at'
            )

            if client_id:
                tailor_security = tailor_security.filter(client_id=client_id)

            for order in tailor_security:
                local_dt = localtime(order['created_at'])

                # Use client name if available, else fallback to invoice ID
                client_name = order['client_name_field']
                name = client_name if client_name else (order['invoice_id_field'] or f"TO{order['order_id_field']}")

                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "security",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": "Security collected",
                        "amount": int(float(order['security_amount'])),
                    }
                ))

        # ---------- PENDING ----------
        if "all" in txn_types or "pendings" in txn_types:
            # Booking pendings
            booking_pendings = BookingV2.objects.filter(
                shop_id=shop_id,
                return_date__date__range=(from_date, to_date),
                payment_status=False,
                deleted__isnull=True
            ).exclude(delivery_status="cancelled"
            ).select_related("client_identity").annotate(
                pending_amount=F("total_amount") - Coalesce(F("discount_amount"), Value(0)) - F("advance_amount"),
                client_name_field=F('client_identity__client_name'),
                invoice_id_field=F('shop_booking_id'),
                booking_id_field=F('id')
            ).filter(
                pending_amount__gt=0
            ).values(
                'client_name_field', 'invoice_id_field', 'booking_id_field',
                'pending_amount', 'return_date'
            )

            if client_id:
                booking_pendings = booking_pendings.filter(client_identity_id=client_id)

            for booking in booking_pendings:
                local_dt = localtime(booking['return_date'])

                # Use client name if available, else fallback to invoice ID
                client_name = booking['client_name_field']
                name = client_name if client_name else (booking['invoice_id_field'] or f"B{booking['booking_id_field']}")

                pending_transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "pending",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": "Pending amount",
                        "amount": booking['pending_amount'],
                    }
                ))

            # Tailor order pendings
            tailor_pendings = TailorOrder.objects.filter(
                shop_id=shop_id,
                deleted__isnull=True
            ).select_related("client").annotate(
                pending_amount=F('customer_total_charge') - F('paid_amount') - Coalesce(F('discount_amount'), Value(0, output_field=models.DecimalField())),
                client_name_field=F('client__client_name'),
                invoice_id_field=F('shop_tailor_id'),
                order_id_field=F('id'),
                date_field=models.Case(
                    models.When(customer_order_type='sale', then=F('pickup_date')),
                    models.When(customer_order_type='rental', then=F('return_date')),
                    default=None,
                    output_field=models.DateTimeField()
                )
            ).filter(
                pending_amount__gt=0,
                date_field__date__range=(from_date, to_date),
                date_field__isnull=False
            ).values(
                'client_name_field', 'invoice_id_field', 'order_id_field',
                'pending_amount', 'date_field'
            )

            if client_id:
                tailor_pendings = tailor_pendings.filter(client_id=client_id)

            for order in tailor_pendings:
                local_dt = localtime(order['date_field'])

                # Use client name if available, else fallback to invoice ID
                client_name = order['client_name_field']
                name = client_name if client_name else (order['invoice_id_field'] or f"TO{order['order_id_field']}")

                pending_transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "pending",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": "Pending amount",
                        "amount": int(float(order['pending_amount'])),
                    }
                ))

        # ---------- BOOKINGS ----------
        if "all" in txn_types or "bookings" in txn_types:
            bookings = BookingV2.objects.filter(
                shop_id=shop_id,
                created_at__date__range=(from_date, to_date),
                deleted__isnull=True
            ).exclude(delivery_status="cancelled"
            ).select_related("client_identity").annotate(
                client_name_field=F('client_identity__client_name'),
                invoice_id_field=F('shop_booking_id'),
                booking_id_field=F('id'),
                effective_amount=F('total_amount') - Coalesce(F('discount_amount'), 0)
            ).values(
                'client_name_field', 'invoice_id_field', 'booking_id_field',
                'total_amount', 'created_at', 'effective_amount'
            )

            if client_id:
                bookings = bookings.filter(client_identity_id=client_id)

            for booking in bookings:
                local_dt = localtime(booking['created_at'])

                # Use client name if available, else fallback to invoice ID
                client_name = booking['client_name_field']
                name = client_name if client_name else (booking['invoice_id_field'] or f"B{booking['booking_id_field']}")

                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "booking",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": booking['invoice_id_field'] or f"B{booking['booking_id_field']}",
                        # "amount": booking['total_amount'],
                        "amount": booking['effective_amount']
                    }
                ))

        # ---------- SALES ----------
        if "all" in txn_types or "sales" in txn_types:
            sale_objects = Sale.objects.filter(
                shop_id=shop_id,
                sale_date__range=(from_date, to_date)
            ).select_related("client").prefetch_related("items__product_variant__product")

            if client_id:
                sale_objects = sale_objects.filter(client_id=client_id)

            for sale in sale_objects:
                items = [item.product_variant.product.name for item in sale.items.all()]
                items_str = ", ".join(items) if items else "No Items"

                # Calculate sort date in Python
                sale_sort_date = make_aware(datetime.combine(sale.sale_date, datetime.min.time()))

                # Use client name if available, else fallback to invoice ID
                client_name = sale.client.client_name if sale.client and sale.client.client_name else None
                name = client_name if client_name else (sale.shop_sale_id or f"SL{sale.id:06d}")

                transactions.append(Txn(
                    sort_date=sale_sort_date,
                    data={
                        "type": "sale",
                        "date": sale.sale_date.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": sale.description or "Sale",
                        "amount": sale.total_amount - sale.discount,
                        "items": items_str,
                    }
                ))

            # Completed tailor orders (sales)
            tailor_objects = TailorOrder.objects.filter(
                shop_id=shop_id,
                customer_order_type='sale',
                status='completed',
                completed_at__date__range=(from_date, to_date),
                deleted__isnull=True
            ).select_related("client").prefetch_related("items")

            if client_id:
                tailor_objects = tailor_objects.filter(client_id=client_id)

            for order in tailor_objects:
                items = [item.product_name for item in order.items.all()]
                items_str = ", ".join(items) if items else "No Items"

                # Use client name if available, else fallback to invoice ID
                client_name = order.client.client_name if order.client and order.client.client_name else None
                name = client_name if client_name else (order.shop_tailor_id or f"TO{order.id:06d}")

                transactions.append(Txn(
                    sort_date=order.completed_at,
                    data={
                        "type": "sale",
                        "date": order.completed_at.strftime("%d-%m-%Y"),
                        "name": name,
                        "description": order.notes or "Tailor Order Sale",
                        "amount": int(float(order.customer_total_charge - (order.discount_amount or 0))),
                        "items": items_str,
                    }
                ))

        # Sort non-pending in descending order
        non_pending_sorted = sorted(transactions, key=lambda t: t.sort_date, reverse=True)

        # Sort pending in ascending order
        pending_sorted = sorted(pending_transactions, key=lambda t: t.sort_date)

        final_transactions = non_pending_sorted + pending_sorted

        return [item.data for item in final_transactions]
