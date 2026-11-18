import logging
from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import (
    Q, F, Value, CharField, Sum, Case, When, DecimalField,
    DateTimeField, OuterRef, Subquery, ExpressionWrapper, Exists
)
from django.db.models.functions import Coalesce, Cast
from django.utils import timezone
from django.utils.timezone import localtime
from django.contrib.postgres.aggregates import StringAgg
from decimal import Decimal

from booking_v2.models import (
    BookedProductVariant, Client, BookingV2, PaymentDetailsV2,
    RefundDetails, SecurityDepositAdjustment, SecurityDepositPayment
)
from expenses_v2.models import Expenses, ProductVariantExpense
from finance.models import Account, ManualIncome
from sales.models import Sale, SalesPaymentDetails
from tailored.models import TailorOrder, CustomerPayment, TailorOrderItem, TailorOrderRefund, TailorOrderSecurityPayment, TailorOrderSecurityDepositAdjustment, TailorPayment
from shop.api.admin_v1.utils.shop import get_active_shop_id
from ERP.utils.mixins import StandardResponseMixin
from booking_v2.utils.ledger import compute_ledger_entries as _compute_ledger_entries

from ..utils.helpers import safe_decimal, parse_date

logger = logging.getLogger("api_requests")


class ShopLedgerTransactionExportMixin(StandardResponseMixin):
    def parse_date(self, date_str):
        return parse_date(date_str)

    def get_transaction_data(self, request, txn_types=None):
        shop_id = get_active_shop_id(request)
        from_date = self.parse_date(request.query_params.get("from_date"))
        to_date = self.parse_date(request.query_params.get("to_date"))
        if txn_types is None:
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
        """
        Fetch transactions based on type with optimized queries.
        Returns a list of transaction dictionaries with all required fields.
        """
        transactions = []

        # Preload all active accounts for this shop — single query, O(1) lookups throughout
        account_map = {
            a['id']: a['name']
            for a in Account.objects.filter(
                shop_id=shop_id, is_active=True, deleted__isnull=True
            ).values('id', 'name')
        }

        # Helper function to format datetime with time in AM/PM format
        def format_datetime_with_ampm(dt):
            if dt:
                local_dt = localtime(dt)
                return local_dt.strftime('%d/%m/%Y %I:%M %p')
            return ''

        def _append_security_payments(payments_qs, id_key, id_field, date_field, sub_type):
            for payment in payments_qs.iterator(chunk_size=2000):
                acct_id = payment['account_id']
                transactions.append({
                    'id': payment['id'],
                    id_key: payment[id_field] or '',
                    'invoice_id': payment[id_field] or '',
                    'date': format_datetime_with_ampm(payment[date_field]),
                    'sort_date': payment[date_field],
                    'type': 'payments',
                    'payment_category': 'security',
                    'name': payment['client_name_field'] or '-',
                    'client_phone': payment['client_phone_field'] or '-',
                    'amount': safe_decimal(payment['amount']),
                    'payment_method': payment['payment_method'] or '',
                    'sub_type': sub_type,
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                })

        # Expense datetime display in 12-hour format with AM/PM
        def format_datetime_with_seconds(dt):
            if dt:
                local_dt = localtime(dt)
                return local_dt.strftime('%d/%m/%Y %I:%M %p')
            return ''

        def format_expense_date_with_time(expense_dt):
            """Use expense_date_time as the single source for expense display datetime."""
            if not expense_dt:
                return ''
            local_dt = localtime(expense_dt)
            return local_dt.strftime('%d/%m/%Y %I:%M %p')

        # Helper function to format date only
        def format_date(dt):
            if dt:
                # Handle both date and datetime objects
                if hasattr(dt, 'hour'):  # It's a datetime
                    local_dt = localtime(dt)
                    return local_dt.strftime('%d/%m/%Y')
                else:  # It's a date
                    return dt.strftime('%d/%m/%Y')
            return ''
        
        # Determine if we need to exclude security entries for Payments+Expenses combination
        exclude_security_in_combined = 'payments' in txn_types and 'expenses' in txn_types

        # ============ BOOKINGS ============
        if 'all' in txn_types or 'bookings' in txn_types:
            # Subquery to get all booked item names concatenated
            booking_items_subquery = BookedProductVariant.objects.filter(
                booking_id=OuterRef('id'),
                deleted__isnull=True
            ).values('booking_id').annotate(
                items=StringAgg('product_name', delimiter=', ', distinct=True, ordering='product_name')
            ).values('items')

            bookings = BookingV2.objects.filter(
                shop_id=shop_id,
                created_at__date__gte=from_date,
                created_at__date__lte=to_date,
                deleted__isnull=True
            ).exclude(
                delivery_status='cancelled'
            ).select_related('client_identity', 'client').annotate(
                client_name_field=Coalesce('client_identity__client_name', 'client__client_name', Value('')),
                client_phone_field=Coalesce(
                    Cast('client_identity__phone_1_e164', CharField()),
                    Cast('client_identity__client_phone_1', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('')
                ),
                effective_amount=F('total_amount') - Coalesce(F('discount_amount'), 0), 
                pending_amount=F('total_amount') - Coalesce(F('discount_amount'), 0) - F('advance_amount'),
                product_names=Subquery(booking_items_subquery)
            ).values(
                'id', 'shop_booking_id', 'created_at', 'client_name_field', 'client_phone_field',
                'advance_amount', 'security_amount', 'pending_amount', 'effective_amount', 'product_names'
            )

            if client_id:
                bookings = bookings.filter(client_identity_id=client_id)

            for booking in bookings:
                transactions.append({
                    'id': booking['id'],
                    'shop_booking_id': booking['shop_booking_id'] or '',
                    'invoice_id': booking['shop_booking_id'] or '',
                    'date': format_date(booking['created_at']),
                    'sort_date': booking['created_at'],
                    'type': 'bookings',
                    'name': booking['client_name_field'] or 'Walk-in',
                    'client_phone': booking['client_phone_field'] or '-',
                    'amount': safe_decimal(booking['effective_amount']),
                    'product_names': booking['product_names'] or '-',
                    'paid_amount': safe_decimal(booking['advance_amount']),
                    # 'paid_amount': safe_decimal(booking['advance_amount']) - safe_decimal(booking['total_refunded']),
                    'pending_amount': safe_decimal(booking['pending_amount']),
                })

            # --- Payment method breakdown for bookings (no N+1) ---
            # Collect IDs directly from the already-iterated queryset so the list is
            # exactly the bookings appended above, regardless of list ordering or future
            # changes to get_transactions() section order.
            booking_ids = [booking['id'] for booking in bookings]
            if booking_ids:
                payments_qs = (
                    PaymentDetailsV2.objects
                    .filter(booking_id__in=booking_ids, deleted__isnull=True)
                    .values('booking_id', 'payment_method')
                    .annotate(total=Sum('payment_amount'))
                )
                refunds_qs = (
                    RefundDetails.objects
                    .filter(booking_id__in=booking_ids)
                    .values('booking_id', 'refund_method')
                    .annotate(total=Sum('refunded_amount'))
                )

                pay_map = {}
                for _p in payments_qs:
                    bid = _p['booking_id']
                    method = 'cash' if 'cash' in (_p['payment_method'] or '').lower() else 'upi'
                    pay_map.setdefault(bid, {'cash': Decimal('0'), 'upi': Decimal('0')})
                    pay_map[bid][method] += safe_decimal(_p['total'])

                ref_map = {}
                for _r in refunds_qs:
                    bid = _r['booking_id']
                    method = 'cash' if 'cash' in (_r['refund_method'] or '').lower() else 'upi'
                    ref_map.setdefault(bid, {'cash': Decimal('0'), 'upi': Decimal('0')})
                    ref_map[bid][method] += safe_decimal(_r['total'])

                for t in transactions:
                    if t.get('type') != 'bookings':
                        continue
                    bid = t['id']
                    pay = pay_map.get(bid, {'cash': Decimal('0'), 'upi': Decimal('0')})
                    ref = ref_map.get(bid, {'cash': Decimal('0'), 'upi': Decimal('0')})
                    # Anchor to advance_amount (the single source of truth for this booking's
                    # total paid, matching the management-command formula:
                    #   advance_amount = max(0, sum(non-deleted payments) - sum(all refunds))
                    # Per-method independent clamping would let an over-refunded method silently
                    # inflate the combined total, so we derive upi as the remainder instead.
                    advance = t.get('paid_amount') or Decimal('0')
                    if not isinstance(advance, Decimal):
                        advance = Decimal(str(advance))
                    raw_cash = pay['cash'] - ref['cash']
                    t['paid_cash'] = max(Decimal('0'), min(raw_cash, advance))
                    t['paid_upi']  = advance - t['paid_cash']

        # ============ PENDINGS ============
        if 'all' in txn_types or 'pendings' in txn_types:
            # Booking pendings
            booking_pendings = BookingV2.objects.filter(
                shop_id=shop_id,
                return_date__date__gte=from_date,
                return_date__date__lte=to_date,
                payment_status=False,
                deleted__isnull=True
            ).exclude(
                delivery_status='cancelled'
            ).select_related('client_identity', 'client').annotate(
                client_name_field=Coalesce('client_identity__client_name', 'client__client_name', Value('')),
                client_phone_field=Coalesce(
                    Cast('client_identity__phone_1_e164', CharField()),
                    Cast('client_identity__client_phone_1', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('')
                ),
                pending_amt=ExpressionWrapper(
                    F('total_amount')
                    - Coalesce(F('discount_amount'), Value(0))
                    - F('advance_amount'),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            ).filter(
                pending_amt__gt=0
            ).values(
                'id', 'shop_booking_id', 'return_date', 'client_name_field',
                'client_phone_field', 'pending_amt'
            )

            if client_id:
                booking_pendings = booking_pendings.filter(client_identity_id=client_id)

            booking_ids = [b['id'] for b in booking_pendings]

            booking_items_map = {}
            if booking_ids:
                for item in (
                    BookedProductVariant.objects
                    .filter(booking_id__in=booking_ids)
                    .values('booking_id', 'product_name')
                ):
                    booking_items_map.setdefault(item['booking_id'], []).append(item['product_name'])

            for pending in booking_pendings:
                items = booking_items_map.get(pending['id'], [])
                items_str = ", ".join(items) or '-'
                if len(items_str) > 50:
                    items_str = items_str[:50] + '...'
                transactions.append({
                    'id': pending['id'],
                    'shop_booking_id': pending['shop_booking_id'] or '',
                    'invoice_id': pending['shop_booking_id'] or '',
                    'date': format_date(pending['return_date']),
                    'sort_date': pending['return_date'],
                    'type': 'pendings',
                    'name': pending['client_name_field'] or '-',
                    'client_phone': pending['client_phone_field'] or '-',
                    'amount': safe_decimal(pending['pending_amt']),
                    'items': items_str,
                })

            # Sales pendings
            sale_pendings = Sale.objects.filter(
                shop_id=shop_id,
                created_at__date__gte=from_date,
                created_at__date__lte=to_date
            ).select_related('client').annotate(
                total_paid=Coalesce(
                    Sum('payments__payment_amount', filter=Q(payments__created_at__isnull=False)),
                    Value(0),
                    output_field=DecimalField()
                ),
                client_name_field=Coalesce('client__client_name', Value('')),
                client_phone_field=Coalesce(
                    Cast('client__phone_1_e164', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('')
                )
            ).annotate(
                pending_amt=ExpressionWrapper(
                    F('paid_amount') - F('total_paid'),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            ).filter(pending_amt__gt=0).values(
                'id', 'shop_sale_id', 'created_at', 'client_name_field',
                'client_phone_field', 'pending_amt'
            )

            if client_id:
                sale_pendings = sale_pendings.filter(client_id=client_id)

            for pending in sale_pendings:
                transactions.append({
                    'id': pending['id'],
                    'shop_sale_id': pending['shop_sale_id'] or '',
                    'invoice_id': pending['shop_sale_id'] or '',
                    'date': format_date(pending['created_at']),
                    'sort_date': pending['created_at'],
                    'type': 'pendings',
                    'name': pending['client_name_field'] or '-',
                    'client_phone': pending['client_phone_field'] or '-',
                    'amount': safe_decimal(pending['pending_amt']),
                })

            # Tailor order pendings
            tailor_pendings = TailorOrder.objects.filter(
                shop_id=shop_id,
                deleted__isnull=True
            ).select_related('client').annotate(
                client_name_field=Coalesce('client__client_name', Value('')),
                client_phone_field=Coalesce(
                    Cast('client__phone_1_e164', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('')
                ),
                pending_amt=ExpressionWrapper(
                    F('customer_total_charge') - F('paid_amount') - Coalesce(F('discount_amount'), Value(0)),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                ),
                date_field=Case(
                    When(customer_order_type='sale', then=F('pickup_date')),
                    When(customer_order_type='rental', then=F('return_date')),
                    default=None,
                    output_field=DateTimeField()
                )
            ).filter(
                pending_amt__gt=0,
                date_field__date__gte=from_date,
                date_field__date__lte=to_date,
                date_field__isnull=False
            ).values(
                'id', 'shop_tailor_id', 'date_field', 'client_name_field',
                'client_phone_field', 'pending_amt'
            )

            if client_id:
                tailor_pendings = tailor_pendings.filter(client_id=client_id)

            tailor_ids = [t['id'] for t in tailor_pendings]

            tailor_items_map = {}
            if tailor_ids:
                for item in (
                    TailorOrderItem.objects
                    .filter(order_id__in=tailor_ids)
                    .values('order_id', 'product_name')
                ):
                    tailor_items_map.setdefault(item['order_id'], []).append(item['product_name'])

            for pending in tailor_pendings:
                items = tailor_items_map.get(pending['id'], [])
                items_str = ", ".join(items) or '-'
                if len(items_str) > 50:
                    items_str = items_str[:50] + '...'
                transactions.append({
                    'id': pending['id'],
                    'shop_tailor_id': pending['shop_tailor_id'] or '',
                    'invoice_id': pending['shop_tailor_id'] or '',
                    'date': format_date(pending['date_field']),
                    'sort_date': pending['date_field'],
                    'type': 'pendings',
                    'name': pending['client_name_field'] or '-',
                    'client_phone': pending['client_phone_field'] or '-',
                    'amount': safe_decimal(pending['pending_amt']),
                    'items': items_str,
                })

        # ============ PAYMENTS ============
        if 'all' in txn_types or 'payments' in txn_types:
            # Build timezone-aware date boundaries for payments (same approach as expenses)
            local_tz = timezone.get_current_timezone()
            payment_day_start = timezone.make_aware(datetime.combine(from_date, datetime.min.time()), local_tz)
            payment_day_end_exclusive = timezone.make_aware(datetime.combine(to_date + timedelta(days=1), datetime.min.time()), local_tz)

            # Booking payments — direct raw entries (no refund-adjusted settlement)
            booking_payments = (
                PaymentDetailsV2.objects
                .filter(
                    booking__shop_id=shop_id,
                    booking__deleted__isnull=True,
                    created_at__gte=payment_day_start,
                    created_at__lt=payment_day_end_exclusive,
                )
                .select_related('booking', 'booking__client_identity', 'booking__client')
                .annotate(
                    client_name_field=Coalesce(
                        'booking__client_identity__client_name',
                        'booking__client__client_name',
                        Value(''),
                    ),
                    client_phone_field=Coalesce(
                        Cast('booking__client_identity__phone_1_e164', CharField()),
                        Cast('booking__client_identity__client_phone_1', CharField()),
                        Value(''),
                    ),
                )
                .values(
                    'id', 'booking__shop_booking_id', 'client_name_field', 'client_phone_field',
                    'payment_amount', 'payment_method', 'account_id', 'created_at',
                )
            )
            if client_id:
                booking_payments = booking_payments.filter(booking__client_identity_id=client_id)

            for payment in booking_payments.iterator(chunk_size=2000):
                acct_id = payment['account_id']
                transactions.append({
                    'id': payment['id'],
                    'shop_booking_id': payment['booking__shop_booking_id'] or '',
                    'invoice_id': payment['booking__shop_booking_id'] or '',
                    'date': format_datetime_with_ampm(payment['created_at']),
                    'sort_date': payment['created_at'],
                    'type': 'payments',
                    'name': payment['client_name_field'] or '-',
                    'client_phone': payment['client_phone_field'] or '-',
                    'amount': safe_decimal(payment['payment_amount']),
                    'payment_method': payment['payment_method'] or '',
                    'sub_type': 'Booking',
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                })

            # # -------- Security Deposit Deductions (Booking) --------
            # security_deductions = SecurityDepositAdjustment.objects.filter(
            #     booking__shop_id=shop_id,
            #     booking__deleted__isnull=True,
            #     adjustment_type='deduction',
            #     created_at__gte=payment_day_start,
            #     created_at__lt=payment_day_end_exclusive
            # ).select_related(
            #     'booking__client_identity',
            #     'booking__client'
            # ).annotate(
            #     client_name_field=Coalesce(
            #         'booking__client_identity__client_name',
            #         'booking__client__client_name',
            #         Value('')
            #     ),
            #     client_phone_field=Coalesce(
            #         Cast('booking__client_identity__phone_1_e164', CharField()),
            #         Cast('booking__client_identity__client_phone_1', CharField()),
            #         Cast('booking__client__client_phone_1', CharField()),
            #         Value('')
            #     ),
            #     booking_id_field=F('booking__id'),
            #     invoice_id_field=F('booking__shop_booking_id'),
            #     payment_amount=F('amount'),
            #     method_field=Coalesce(
            #         F('payment_method'),
            #         Value('cash', output_field=CharField())
            #     ),
            # ).values(
            #     'booking_id_field',
            #     'invoice_id_field',
            #     'client_name_field',
            #     'client_phone_field',
            #     'payment_amount',
            #     'method_field',
            #     'created_at'
            # )

            # if client_id:
            #     security_deductions = security_deductions.filter(
            #         booking__client_identity_id=client_id
            #     )

            # for deduction in security_deductions:
            #     transactions.append({
            #         'id': deduction['booking_id_field'],
            #         'shop_booking_id': deduction['invoice_id_field'] or '',
            #         'date': format_datetime_with_ampm(deduction['created_at']),
            #         'sort_date': deduction['created_at'],
            #         'type': 'payments',
            #         'name': deduction['client_name_field'] or '-',
            #         'client_phone': deduction['client_phone_field'] or '-',
            #         'amount': safe_decimal(deduction['payment_amount']),
            #         'payment_method': deduction['method_field'] or '',
            #         'account_id': None,
            #         'account_name': None,
            #     })

            # Sales payments
            sales_payments = SalesPaymentDetails.objects.filter(
                sale__shop_id=shop_id,
                created_at__gte=payment_day_start,
                created_at__lt=payment_day_end_exclusive
            ).select_related('sale__client').values(
                'sale__id', 'sale__shop_sale_id', 'payment_method', 'account_id', 'created_at'
            ).annotate(
                client_name_field=Coalesce('sale__client__client_name', Value('')),
                client_phone_field=Coalesce(
                    Cast('sale__client__phone_1_e164', CharField()),
                    Cast('sale__client__client_phone_1', CharField()),
                    Value('')
                ),
                payment_amount=F('payment_amount')
            )

            if client_id:
                sales_payments = sales_payments.filter(sale__client_id=client_id)

            for payment in sales_payments:
                transactions.append({
                    'id': payment['sale__id'],
                    'shop_sale_id': payment['sale__shop_sale_id'] or '',
                    'invoice_id': payment['sale__shop_sale_id'] or '',
                    'date': format_datetime_with_ampm(payment['created_at']),
                    'sort_date': payment['created_at'],
                    'type': 'payments',
                    'name': payment['client_name_field'] or payment['sale__shop_sale_id'],
                    'client_phone': payment['client_phone_field'] or '-',
                    'amount': safe_decimal(payment['payment_amount']),
                    'payment_method': payment['payment_method'] or '',
                    'sub_type': 'Sale',
                    'account_id': payment['account_id'],
                    'account_name': account_map.get(payment['account_id']),
                })

            # Tailor order payments — direct raw entries (no refund-adjusted settlement)
            tailor_payments = (
                CustomerPayment.objects
                .filter(
                    order__shop_id=shop_id,
                    order__deleted__isnull=True,
                    order__created_booking__isnull=True,
                    created_at__gte=payment_day_start,
                    created_at__lt=payment_day_end_exclusive,
                )
                .select_related('order', 'order__client')
                .annotate(
                    client_name_field=Coalesce('order__client__client_name', Value('')),
                    client_phone_field=Coalesce(
                        Cast('order__client__phone_1_e164', CharField()),
                        Cast('order__client__client_phone_1', CharField()),
                        Value(''),
                    ),
                )
                .values(
                    'id', 'order__shop_tailor_id', 'client_name_field', 'client_phone_field',
                    'payment_amount', 'payment_method', 'account_id', 'created_at',
                )
            )
            if client_id:
                tailor_payments = tailor_payments.filter(order__client_id=client_id)

            for payment in tailor_payments.iterator(chunk_size=2000):
                acct_id = payment['account_id']
                transactions.append({
                    'id': payment['id'],
                    'shop_tailor_id': payment['order__shop_tailor_id'] or '',
                    'invoice_id': payment['order__shop_tailor_id'] or '',
                    'date': format_datetime_with_ampm(payment['created_at']),
                    'sort_date': payment['created_at'],
                    'type': 'payments',
                    'name': payment['client_name_field'] or '-',
                    'client_phone': payment['client_phone_field'] or '-',
                    'amount': safe_decimal(payment['payment_amount']),
                    'payment_method': payment['payment_method'] or '',
                    'sub_type': 'Custom Work',
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                })

            if not exclude_security_in_combined:
                # Booking security payments — only paid security entries use paid_at instead of created_at
                booking_security_payments = (
                    SecurityDepositPayment.objects
                    .filter(
                        booking__shop_id=shop_id,
                        booking__deleted__isnull=True,
                        is_paid=True,
                        paid_at__gte=payment_day_start,
                        paid_at__lt=payment_day_end_exclusive,
                    )
                    .select_related('booking', 'booking__client_identity')
                    .annotate(
                        client_name_field=Coalesce(
                            'booking__client_identity__client_name',
                            Value(''),
                        ),
                        client_phone_field=Coalesce(
                            Cast('booking__client_identity__phone_1_e164', CharField()),
                            Cast('booking__client_identity__client_phone_1', CharField()),
                            Value(''),
                        ),
                    )
                    .values(
                        'id', 'booking__shop_booking_id', 'client_name_field', 'client_phone_field',
                        'amount', 'payment_method', 'account_id', 'paid_at',
                    )
                )
                if client_id:
                    booking_security_payments = booking_security_payments.filter(booking__client_identity_id=client_id)

                _append_security_payments(
                    booking_security_payments,
                    id_key='shop_booking_id',
                    id_field='booking__shop_booking_id',
                    date_field='paid_at',
                    sub_type='Booking Security',
                )

            if not exclude_security_in_combined:
                # Tailor order security payments — only paid entries and use paid_at
                tailor_security_payments = (
                    TailorOrderSecurityPayment.objects
                    .filter(
                        order__shop_id=shop_id,
                        order__deleted__isnull=True,
                        order__created_booking__isnull=True,
                        is_paid=True,
                        paid_at__isnull=False,
                        paid_at__gte=payment_day_start,
                        paid_at__lt=payment_day_end_exclusive,
                    )
                    .select_related('order', 'order__client')
                    .annotate(
                        client_name_field=Coalesce('order__client__client_name', Value('')),
                        client_phone_field=Coalesce(
                            Cast('order__client__phone_1_e164', CharField()),
                            Cast('order__client__client_phone_1', CharField()),
                            Value(''),
                        ),
                    )
                    .values(
                        'id', 'order__shop_tailor_id', 'client_name_field', 'client_phone_field',
                        'amount', 'payment_method', 'account_id', 'paid_at',
                    )
                )
                if client_id:
                    tailor_security_payments = tailor_security_payments.filter(order__client_id=client_id)

                _append_security_payments(
                    tailor_security_payments,
                    id_key='shop_tailor_id',
                    id_field='order__shop_tailor_id',
                    date_field='paid_at',
                    sub_type='Custom Work Security',
                )

            # Manual Income entries — excluded when client_id filter is active
            if not client_id:
                manual_incomes = (
                    ManualIncome.objects
                    .filter(
                        shop_id=shop_id,
                        transaction_date_time__gte=payment_day_start,
                        transaction_date_time__lt=payment_day_end_exclusive,
                    )
                    .select_related('category')
                    .values('id', 'amount', 'payment_method', 'account_id', 'transaction_date_time', 'category__name')
                )
                for income in manual_incomes.iterator(chunk_size=2000):
                    acct_id = income['account_id']
                    transactions.append({
                        'id': income['id'],
                        'invoice_id': '-',
                        'date': format_datetime_with_ampm(income['transaction_date_time']),
                        'sort_date': income['transaction_date_time'],
                        'type': 'payments',
                        'name': income['category__name'] or 'Manual Entry',
                        'client_phone': '',
                        'amount': safe_decimal(income['amount']),
                        'payment_method': income['payment_method'] or '',
                        'sub_type': 'Manual Entry',
                        'account_id': acct_id,
                        'account_name': account_map.get(acct_id),
                    })

        # ============ PAYMENTS WITHOUT SECURITY ============
        if 'all' in txn_types or 'payments_without_security' in txn_types:
            # Build timezone-aware date boundaries for payments (same approach as expenses)
            local_tz = timezone.get_current_timezone()
            payment_day_start = timezone.make_aware(datetime.combine(from_date, datetime.min.time()), local_tz)
            payment_day_end_exclusive = timezone.make_aware(datetime.combine(to_date + timedelta(days=1), datetime.min.time()), local_tz)

            # Booking payments — direct raw entries (no refund-adjusted settlement)
            booking_payments = (
                PaymentDetailsV2.objects
                .filter(
                    booking__shop_id=shop_id,
                    booking__deleted__isnull=True,
                    created_at__gte=payment_day_start,
                    created_at__lt=payment_day_end_exclusive,
                )
                .select_related('booking', 'booking__client_identity', 'booking__client')
                .annotate(
                    client_name_field=Coalesce(
                        'booking__client_identity__client_name',
                        'booking__client__client_name',
                        Value(''),
                    ),
                    client_phone_field=Coalesce(
                        Cast('booking__client_identity__phone_1_e164', CharField()),
                        Cast('booking__client_identity__client_phone_1', CharField()),
                        Value(''),
                    ),
                )
                .values(
                    'id', 'booking__shop_booking_id', 'client_name_field', 'client_phone_field',
                    'payment_amount', 'payment_method', 'account_id', 'created_at',
                )
            )
            if client_id:
                booking_payments = booking_payments.filter(booking__client_identity_id=client_id)

            for payment in booking_payments.iterator(chunk_size=2000):
                acct_id = payment['account_id']
                transactions.append({
                    'id': payment['id'],
                    'shop_booking_id': payment['booking__shop_booking_id'] or '',
                    'invoice_id': payment['booking__shop_booking_id'] or '',
                    'date': format_datetime_with_ampm(payment['created_at']),
                    'sort_date': payment['created_at'],
                    'type': 'payments_without_security',
                    'name': payment['client_name_field'] or '-',
                    'client_phone': payment['client_phone_field'] or '-',
                    'amount': safe_decimal(payment['payment_amount']),
                    'payment_method': payment['payment_method'] or '',
                    'sub_type': 'Booking',
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                })

            # Sales payments
            sales_payments = SalesPaymentDetails.objects.filter(
                sale__shop_id=shop_id,
                created_at__gte=payment_day_start,
                created_at__lt=payment_day_end_exclusive
            ).select_related('sale__client').values(
                'sale__id', 'sale__shop_sale_id', 'payment_method', 'account_id', 'created_at'
            ).annotate(
                client_name_field=Coalesce('sale__client__client_name', Value('')),
                client_phone_field=Coalesce(
                    Cast('sale__client__phone_1_e164', CharField()),
                    Cast('sale__client__client_phone_1', CharField()),
                    Value('')
                ),
                payment_amount=F('payment_amount')
            )

            if client_id:
                sales_payments = sales_payments.filter(sale__client_id=client_id)

            for payment in sales_payments:
                transactions.append({
                    'id': payment['sale__id'],
                    'shop_sale_id': payment['sale__shop_sale_id'] or '',
                    'invoice_id': payment['sale__shop_sale_id'] or '',
                    'date': format_datetime_with_ampm(payment['created_at']),
                    'sort_date': payment['created_at'],
                    'type': 'payments_without_security',
                    'name': payment['client_name_field'] or payment['sale__shop_sale_id'],
                    'client_phone': payment['client_phone_field'] or '-',
                    'amount': safe_decimal(payment['payment_amount']),
                    'payment_method': payment['payment_method'] or '',
                    'sub_type': 'Sale',
                    'account_id': payment['account_id'],
                    'account_name': account_map.get(payment['account_id']),
                })

            # Tailor order payments — direct raw entries (no refund-adjusted settlement)
            tailor_payments = (
                CustomerPayment.objects
                .filter(
                    order__shop_id=shop_id,
                    order__deleted__isnull=True,
                    order__created_booking__isnull=True,
                    created_at__gte=payment_day_start,
                    created_at__lt=payment_day_end_exclusive,
                )
                .select_related('order', 'order__client')
                .annotate(
                    client_name_field=Coalesce('order__client__client_name', Value('')),
                    client_phone_field=Coalesce(
                        Cast('order__client__phone_1_e164', CharField()),
                        Cast('order__client__client_phone_1', CharField()),
                        Value(''),
                    ),
                )
                .values(
                    'id', 'order__shop_tailor_id', 'client_name_field', 'client_phone_field',
                    'payment_amount', 'payment_method', 'account_id', 'created_at',
                )
            )
            if client_id:
                tailor_payments = tailor_payments.filter(order__client_id=client_id)

            for payment in tailor_payments.iterator(chunk_size=2000):
                acct_id = payment['account_id']
                transactions.append({
                    'id': payment['id'],
                    'shop_tailor_id': payment['order__shop_tailor_id'] or '',
                    'invoice_id': payment['order__shop_tailor_id'] or '',
                    'date': format_datetime_with_ampm(payment['created_at']),
                    'sort_date': payment['created_at'],
                    'type': 'payments_without_security',
                    'name': payment['client_name_field'] or '-',
                    'client_phone': payment['client_phone_field'] or '-',
                    'amount': safe_decimal(payment['payment_amount']),
                    'payment_method': payment['payment_method'] or '',
                    'sub_type': 'Custom Work',
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                })

            # Manual Income entries — excluded when client_id filter is active
            if not client_id:
                manual_incomes = (
                    ManualIncome.objects
                    .filter(
                        shop_id=shop_id,
                        transaction_date_time__gte=payment_day_start,
                        transaction_date_time__lt=payment_day_end_exclusive,
                    )
                    .select_related('category')
                    .values('id', 'amount', 'payment_method', 'account_id', 'transaction_date_time', 'category__name')
                )
                for income in manual_incomes.iterator(chunk_size=2000):
                    acct_id = income['account_id']
                    transactions.append({
                        'id': income['id'],
                        'invoice_id': '-',
                        'date': format_datetime_with_ampm(income['transaction_date_time']),
                        'sort_date': income['transaction_date_time'],
                        'type': 'payments_without_security',
                        'name': income['category__name'] or 'Manual Entry',
                        'client_phone': '',
                        'amount': safe_decimal(income['amount']),
                        'payment_method': income['payment_method'] or '',
                        'sub_type': 'Manual Entry',
                        'account_id': acct_id,
                        'account_name': account_map.get(acct_id),
                    })                    

        # ============ EXPENSES ============
        if 'all' in txn_types or 'expenses' in txn_types:
            # General expenses
            local_tz = timezone.get_current_timezone()
            expense_day_start = timezone.make_aware(
                datetime.combine(from_date, datetime.min.time()),
                local_tz,
            )
            expense_day_end_exclusive = timezone.make_aware(
                datetime.combine(to_date + timedelta(days=1), datetime.min.time()),
                local_tz,
            )
            expense_date_filter = (
                Q(expense_date_time__gte=expense_day_start, expense_date_time__lt=expense_day_end_exclusive)
            )

            general_expenses = Expenses.objects.filter(
                shop_id=shop_id,
            ).filter(expense_date_filter).values(
                'id', 'date', 'expense_date_time', 'created_at', 'type', 'description', 'expense', 'payment_method', 'account_id'
            )

            for expense in general_expenses:
                effective_date = expense['expense_date_time']
                transactions.append({
                    'id': expense['id'],
                    'date': format_expense_date_with_time(effective_date),
                    'sort_date': effective_date,
                    'type': 'expenses',
                    'name': expense['type'] or 'General Expense',
                    'description': expense['description'] or '',
                    'amount': safe_decimal(expense['expense']),
                    'payment_method': expense['payment_method'] or '-',
                    'account_id': expense['account_id'],
                    'account_name': account_map.get(expense['account_id']),
                    'expense_type': expense['type'] or 'General Expense',
                    'sub_type': 'Expense',
                    'entity_name': None,
                })

            # Product variant expenses
            variant_expenses = ProductVariantExpense.objects.filter(
                shop_id=shop_id,
            ).filter(expense_date_filter).select_related('product_variant__product').values(
                'id', 'date', 'expense_date_time', 'created_at', 'product_variant__product__name',
                'product_variant__attribute', 'expense', 'payment_method', 'account_id'
            )

            for expense in variant_expenses:
                effective_date = expense['expense_date_time']
                transactions.append({
                    'id': expense['id'],
                    'date': format_expense_date_with_time(effective_date),
                    'sort_date': effective_date,
                    'type': 'expenses',
                    'name': expense.get('product_variant__product__name') or 'Product Expense',
                    'amount': safe_decimal(expense['expense']),
                    'payment_method': expense['payment_method'] or '-',
                    'account_id': expense['account_id'],
                    'account_name': account_map.get(expense['account_id']),
                    'expense_type': expense.get('type') or 'Product Expense',
                    'sub_type': 'Product Expense',
                    'entity_name': None,
                })

            # Booking refunds
            booking_refunds = (
                RefundDetails.objects
                .filter(
                    booking__shop_id=shop_id,
                    booking__deleted__isnull=True,
                    created_at__gte=expense_day_start,
                    created_at__lt=expense_day_end_exclusive,
                )
                .values('id', 'booking_id', 'refunded_amount', 'refund_reason', 'refund_method', 'account_id', 'created_at')
            )
            for refund in booking_refunds.iterator(chunk_size=2000):
                effective_date = refund['created_at']
                acct_id = refund['account_id']
                transactions.append({
                    'id': refund['booking_id'],
                    'date': format_expense_date_with_time(effective_date),
                    'sort_date': effective_date,
                    'type': 'expenses',
                    'name': 'Booking Refund',
                    'description': refund['refund_reason'] or '',
                    'amount': safe_decimal(refund['refunded_amount']),
                    'payment_method': refund['refund_method'] or '-',
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                    'expense_type': 'Booking Refund',
                    'sub_type': 'Booking Refund',
                    'entity_name': None,
                })

            # Tailor order refunds
            tailor_refunds_expense = (
                TailorOrderRefund.objects
                .filter(
                    order__shop_id=shop_id,
                    order__deleted__isnull=True,
                    order__created_booking__isnull=True,
                    created_at__gte=expense_day_start,
                    created_at__lt=expense_day_end_exclusive,
                )
                .values('id', 'order_id', 'refunded_amount', 'refund_reason', 'refund_method', 'account_id', 'created_at')
            )
            for refund in tailor_refunds_expense.iterator(chunk_size=2000):
                effective_date = refund['created_at']
                acct_id = refund['account_id']
                transactions.append({
                    'id': refund['order_id'],
                    'date': format_expense_date_with_time(effective_date),
                    'sort_date': effective_date,
                    'type': 'expenses',
                    'name': 'Custom work Refund',
                    'description': refund['refund_reason'] or '',
                    'amount': safe_decimal(refund['refunded_amount']),
                    'payment_method': refund['refund_method'] or '-',
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                    'expense_type': 'Custom work Refund',
                    'sub_type': 'Custom work Refund',
                    'entity_name': None,
                })

            if not exclude_security_in_combined:
                # Booking security refunds
                booking_security_refunds = (
                    SecurityDepositAdjustment.objects
                    .filter(
                        booking__shop_id=shop_id,
                        booking__deleted__isnull=True,
                        adjustment_type='refund',
                        created_at__gte=expense_day_start,
                        created_at__lt=expense_day_end_exclusive,
                    )
                    .select_related('booking')
                    .values('id', 'booking_id', 'amount', 'reason', 'payment_method', 'account_id', 'created_at')
                )
                for refund in booking_security_refunds.iterator(chunk_size=2000):
                    effective_date = refund['created_at']
                    acct_id = refund['account_id']
                    transactions.append({
                        'id': refund['booking_id'],
                        'date': format_expense_date_with_time(effective_date),
                        'sort_date': effective_date,
                        'type': 'expenses',
                        'name': 'Booking Security Refund',
                        'description': refund['reason'] or '',
                        'amount': safe_decimal(refund['amount']),
                        'payment_method': refund['payment_method'] or '-',
                        'account_id': acct_id,
                        'account_name': account_map.get(acct_id),
                        'expense_type': 'Booking Security Refund',
                        'sub_type': 'Booking Security Refund',
                        'entity_name': None,
                    })

            if not exclude_security_in_combined:
                # Tailor order security refunds
                tailor_security_refunds = (
                    TailorOrderSecurityDepositAdjustment.objects
                    .filter(
                        order__shop_id=shop_id,
                        order__deleted__isnull=True,
                        order__created_booking__isnull=True,
                        adjustment_type='refunded',
                        created_at__gte=expense_day_start,
                        created_at__lt=expense_day_end_exclusive,
                    )
                    .values('id', 'order_id', 'amount', 'reason', 'payment_method', 'account_id', 'created_at')
                )
                for refund in tailor_security_refunds.iterator(chunk_size=2000):
                    effective_date = refund['created_at']
                    acct_id = refund['account_id']
                    transactions.append({
                        'id': refund['order_id'],
                        'date': format_expense_date_with_time(effective_date),
                        'sort_date': effective_date,
                        'type': 'expenses',
                        'name': 'Custom work Security Refund',
                        'description': refund['reason'] or '',
                        'amount': safe_decimal(refund['amount']),
                        'payment_method': refund['payment_method'] or '-',
                        'account_id': acct_id,
                        'account_name': account_map.get(acct_id),
                        'expense_type': 'Custom work Security Refund',
                        'sub_type': 'Custom work Security Refund',
                        'entity_name': None,
                    })

            # Tailor payments (money paid out to tailors)
            tailor_payments_expense = (
                TailorPayment.objects
                .filter(
                    shop_id=shop_id,
                    transaction_date_time__gte=expense_day_start,
                    transaction_date_time__lt=expense_day_end_exclusive,
                )
                .select_related("category", "tailor")
                .values(
                    "id", "amount", "notes", "payment_method", "account_id",
                    "transaction_date_time", "category__name", "tailor__name",
                )
            )
            for payment in tailor_payments_expense.iterator(chunk_size=2000):
                effective_date = payment["transaction_date_time"]
                acct_id = payment["account_id"]
                transactions.append({
                    'id': payment['id'],
                    'date': format_expense_date_with_time(effective_date),
                    'sort_date': effective_date,
                    'type': 'expenses',
                    'name': payment['category__name'] or 'Tailor Payment',
                    'description': payment['notes'] or '',
                    'amount': safe_decimal(payment['amount']),
                    'payment_method': payment['payment_method'] or '-',
                    'account_id': acct_id,
                    'account_name': account_map.get(acct_id),
                    'expense_type': payment['category__name'] or 'Tailor Payment',
                    'sub_type': 'Tailor Payment',
                    'entity_name': payment['tailor__name'],
                })

        # ============ SALES ============
        if 'all' in txn_types or 'sales' in txn_types:
            # Regular sales with optimized subqueries (NO N+1)
            from sales.models import SaleItem

            # Subquery for payment method
            sale_payment_method_subquery = SalesPaymentDetails.objects.filter(
                sale=OuterRef('pk')
            ).order_by('created_at').values('payment_method')[:1]

            # Subquery for first account_id (same order as payment method)
            sale_account_id_subquery = SalesPaymentDetails.objects.filter(
                sale=OuterRef('pk')
            ).order_by('created_at').values('account_id')[:1]

            # Subquery for product names (aggregated in single query)
            sale_items_subquery = SaleItem.objects.filter(
                sale=OuterRef('pk')
            ).select_related('product_variant__product').values('sale').annotate(
                items=StringAgg('product_variant__product__name', delimiter=', ', distinct=True, ordering='product_variant__product__name')
            ).values('items')

            sale_objects = Sale.objects.filter(
                shop_id=shop_id,
                sale_date__gte=from_date,
                sale_date__lte=to_date
            ).select_related('client').annotate(
                first_payment_method=Subquery(sale_payment_method_subquery),
                first_account_id=Subquery(sale_account_id_subquery),
                product_names=Subquery(sale_items_subquery),
                client_name_field=Coalesce('client__client_name', Value('Walk-in')),
                client_phone_field=Coalesce(
                    Cast('client__phone_1_e164', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('-')
                ),
                effective_amount=ExpressionWrapper(
                    F('total_amount') - F('discount'),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            ).values(
                'id', 'shop_sale_id', 'sale_date', 'client_name_field',
                'client_phone_field', 'effective_amount', 'product_names',
                'first_payment_method', 'first_account_id', 'description'
            )

            if client_id:
                sale_objects = sale_objects.filter(client_id=client_id)

            for sale in sale_objects:
                transactions.append({
                    'id': sale['id'],
                    'shop_sale_id': sale['shop_sale_id'] or '',
                    'invoice_id': sale['shop_sale_id'] or '',
                    'date': format_date(sale['sale_date']),
                    'sort_date': sale['sale_date'],
                    'type': 'sales',
                    'name': sale['client_name_field'],
                    'client_phone': sale['client_phone_field'],
                    'amount': safe_decimal(sale['effective_amount']),
                    'product_names': sale['product_names'] or 'No Items',
                    'payment_method': sale['first_payment_method'] or '',
                    'account_id': sale['first_account_id'],
                    'account_name': account_map.get(sale['first_account_id']),
                    'description': sale['description'] or 'Sale',
                })

            # Completed tailor orders (sales) with optimized subqueries (NO N+1)

            # Subquery for payment method
            tailor_payment_method_subquery = CustomerPayment.objects.filter(
                order=OuterRef('pk')
            ).order_by('created_at').values('payment_method')[:1]

            # Subquery for first account_id (same order as payment method)
            tailor_account_id_subquery = CustomerPayment.objects.filter(
                order=OuterRef('pk')
            ).order_by('created_at').values('account_id')[:1]

            # Subquery for tailor order item names (aggregated in single query)
            tailor_items_subquery = TailorOrderItem.objects.filter(
                order=OuterRef('pk'),
                deleted__isnull=True
            ).values('order').annotate(
                items=StringAgg('product_name', delimiter=', ', distinct=True, ordering='product_name')
            ).values('items')

            tailor_objects = TailorOrder.objects.filter(
                shop_id=shop_id,
                customer_order_type='sale',
                status='completed',
                completed_at__date__gte=from_date,
                completed_at__date__lte=to_date,
                deleted__isnull=True
            ).select_related('client').annotate(
                first_payment_method=Subquery(tailor_payment_method_subquery),
                first_account_id=Subquery(tailor_account_id_subquery),
                product_names=Subquery(tailor_items_subquery),
                client_name_field=Coalesce('client__client_name', Value('Customer')),
                client_phone_field=Coalesce(
                    Cast('client__phone_1_e164', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('-')
                ),
                effective_amount=ExpressionWrapper(
                    F('customer_total_charge') - Coalesce(F('discount_amount'), Value(0)),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            ).values(
                'id', 'shop_tailor_id', 'completed_at', 'client_name_field',
                'client_phone_field', 'effective_amount', 'product_names',
                'first_payment_method', 'first_account_id', 'notes'
            )

            if client_id:
                tailor_objects = tailor_objects.filter(client_id=client_id)

            for order in tailor_objects:
                transactions.append({
                    'id': order['id'],
                    'shop_tailor_id': order['shop_tailor_id'] or '',
                    'invoice_id': order['shop_tailor_id'] or '',
                    'date': format_date(order['completed_at']),
                    'sort_date': order['completed_at'],
                    'type': 'sales',
                    'name': order['client_name_field'],
                    'client_phone': order['client_phone_field'],
                    'amount': safe_decimal(order['effective_amount']),
                    'product_names': order['product_names'] or 'No Items',
                    'payment_method': order['first_payment_method'] or '',
                    'account_id': order['first_account_id'],
                    'account_name': account_map.get(order['first_account_id']),
                    'description': order['notes'] or 'Tailor Order Sale',
                })

        # ============ SECURITY ============
        if 'all' in txn_types or 'security' in txn_types:
            # Subquery for total refunded amount per booking
            refund_subquery = SecurityDepositAdjustment.objects.filter(
                booking=OuterRef('pk'),
                adjustment_type='refund'
            ).values('booking').annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(0, output_field=DecimalField())
                )
            ).values('total')[:1]

            # Subquery for total deducted amount per booking
            deduction_subquery = SecurityDepositAdjustment.objects.filter(
                booking=OuterRef('pk'),
                adjustment_type='deduction'
            ).values('booking').annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(0, output_field=DecimalField())
                )
            ).values('total')[:1]

            # Check if booking has any adjustments
            has_adjustment_subquery = SecurityDepositAdjustment.objects.filter(
                booking=OuterRef('pk')
            )

            # Check if booking has any paid security payments
            has_payment_subquery = SecurityDepositPayment.objects.filter(
                booking=OuterRef('pk'),
                is_paid=True,
            )

            # Latest paid security payment method and account
            payment_method_subquery = SecurityDepositPayment.objects.filter(
                booking=OuterRef('pk'),
                is_paid=True,
            ).order_by('-created_at').values('payment_method')[:1]

            security_account_id_subquery = SecurityDepositPayment.objects.filter(
                booking=OuterRef('pk'),
                is_paid=True,
            ).order_by('-created_at').values('account_id')[:1]

            booking_security = BookingV2.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                deleted__isnull=True,
                return_date__isnull=False,
                return_date__date__gte=from_date,
                return_date__date__lte=to_date,
                security_deposit_payments__is_paid=True,
            ).exclude(
                delivery_status='cancelled'  # Exclude cancelled bookings
            ).distinct().select_related(
                'client_identity', 'client'
            ).annotate(
                has_adjustment=Exists(has_adjustment_subquery),
                has_payment=Exists(has_payment_subquery),

                client_name_field=Coalesce(
                    'client_identity__client_name',
                    'client__client_name',
                    Value('Walk-in')
                ),
                client_phone_field=Coalesce(
                    Cast('client_identity__phone_1_e164', CharField()),
                    Cast('client_identity__client_phone_1', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('')
                ),

                # ===== Refunded Logic =====
                refunded_amount=Case(
                    # If has adjustments, use the sum from adjustments
                    When(
                        has_adjustment=True,
                        then=Coalesce(
                            Subquery(refund_subquery),
                            Value(0, output_field=DecimalField())
                        )
                    ),
                    # If completed AND no adjustments AND no payments, treat full amount as refunded
                    When(
                        Q(has_adjustment=False) &
                        Q(has_payment=False) &
                        Q(booking_status='completed'),
                        then=Cast(F('security_amount'), DecimalField(max_digits=10, decimal_places=2))
                    ),
                    # Default case
                    default=Value(0, output_field=DecimalField()),
                    output_field=DecimalField()
                ),

                # ===== Deducted Logic =====
                deducted_amount=Case(
                    # If has adjustments, use the sum from adjustments
                    When(
                        has_adjustment=True,
                        then=Coalesce(
                            Subquery(deduction_subquery),
                            Value(0, output_field=DecimalField())
                        )
                    ),
                    # Default case
                    default=Value(0, output_field=DecimalField()),
                    output_field=DecimalField()
                ),

                # ===== Pending Logic =====
                pending_amount=ExpressionWrapper(
                    Cast(F('security_amount'), DecimalField(max_digits=10, decimal_places=2)) -
                    Coalesce(
                        Subquery(refund_subquery),
                        Value(0, output_field=DecimalField())
                    ) -
                    Coalesce(
                        Subquery(deduction_subquery),
                        Value(0, output_field=DecimalField())
                    ),
                    output_field=DecimalField()
                ),

                # ===== Payment Method =====
                payment_method_field=Coalesce(
                    Subquery(payment_method_subquery),
                    Value('-')
                ),
                security_account_id=Subquery(security_account_id_subquery),
            ).values(
                'id',
                'shop_booking_id',
                'created_at',
                'return_date',
                'client_name_field',
                'client_phone_field',
                'security_amount',
                'refunded_amount',
                'deducted_amount',
                'pending_amount',
                'payment_method_field',
                'security_account_id',
            )

            if client_id:
                booking_security = booking_security.filter(client_identity_id=client_id)

            for security in booking_security:
                transactions.append({
                    'id': security['id'],
                    'shop_booking_id': security['shop_booking_id'] or '',
                    'date': format_date(security['return_date']),
                    'sort_date': security['return_date'],
                    'type': 'security',
                    'name': security['client_name_field'],
                    'client_phone': security['client_phone_field'] or '-',
                    'amount': safe_decimal(security['security_amount']),
                    'refunded_amount': safe_decimal(security['refunded_amount']),
                    'deducted_amount': safe_decimal(security['deducted_amount']),
                    'pending_amount': safe_decimal(security['pending_amount']),
                    'payment_method': security['payment_method_field'],
                    'account_id': security['security_account_id'],
                    'account_name': account_map.get(security['security_account_id']),
                })

            # ---------- TAILOR ORDER SECURITY ----------
            # Only include tailor orders that haven't been converted to bookings AND have paid security
            # Subquery for total refunded amount per tailor order
            tailor_refund_subquery = TailorOrderSecurityDepositAdjustment.objects.filter(
                order=OuterRef('pk'),
                adjustment_type='refunded'
            ).values('order').annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(0, output_field=DecimalField())
                )
            ).values('total')[:1]

            # Subquery for total deducted amount per tailor order
            tailor_deduction_subquery = TailorOrderSecurityDepositAdjustment.objects.filter(
                order=OuterRef('pk'),
                adjustment_type='deducted'
            ).values('order').annotate(
                total=Coalesce(
                    Sum('amount'),
                    Value(0, output_field=DecimalField())
                )
            ).values('total')[:1]

            # Check if tailor order has any adjustments
            tailor_has_adjustment_subquery = TailorOrderSecurityDepositAdjustment.objects.filter(
                order=OuterRef('pk')
            )

            # Check if tailor order has any paid security payments
            tailor_has_payment_subquery = TailorOrderSecurityPayment.objects.filter(
                order=OuterRef('pk'),
                is_paid=True,
            )

            # Latest paid security payment method and account for tailor order
            tailor_payment_method_subquery = TailorOrderSecurityPayment.objects.filter(
                order=OuterRef('pk'),
                is_paid=True,
            ).order_by('-created_at').values('payment_method')[:1]

            tailor_security_account_id_subquery = TailorOrderSecurityPayment.objects.filter(
                order=OuterRef('pk'),
                is_paid=True,
            ).order_by('-created_at').values('account_id')[:1]

            tailor_security = TailorOrder.objects.filter(
                shop_id=shop_id,
                security_amount__gt=0,
                customer_order_type="rental",
                created_booking__isnull=True,  # Not converted to booking
                deleted__isnull=True,
                return_date__isnull=False,
                return_date__date__gte=from_date,
                return_date__date__lte=to_date,
                security_payments__is_paid=True,  # Only include paid security
            ).distinct().select_related(
                'client'
            ).annotate(
                has_adjustment=Exists(tailor_has_adjustment_subquery),
                has_payment=Exists(tailor_has_payment_subquery),
                client_name_field=Coalesce(
                    'client__client_name',
                    Value('Customer')
                ),
                client_phone_field=Coalesce(
                    Cast('client__phone_1_e164', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('')
                ),

                # ===== Refunded Logic =====
                refunded_amount=Case(
                    # If has adjustments, use the sum from adjustments
                    When(
                        has_adjustment=True,
                        then=Coalesce(
                            Subquery(tailor_refund_subquery),
                            Value(0, output_field=DecimalField())
                        )
                    ),
                    # If completed AND no adjustments AND no payments, treat full amount as refunded
                    When(
                        Q(has_adjustment=False) &
                        Q(has_payment=False) &
                        Q(status='completed'),
                        then=Cast(F('security_amount'), DecimalField(max_digits=10, decimal_places=2))
                    ),
                    # Default case
                    default=Value(0, output_field=DecimalField()),
                    output_field=DecimalField()
                ),

                # ===== Deducted Logic =====
                deducted_amount=Case(
                    # If has adjustments, use the sum from adjustments
                    When(
                        has_adjustment=True,
                        then=Coalesce(
                            Subquery(tailor_deduction_subquery),
                            Value(0, output_field=DecimalField())
                        )
                    ),
                    # Default case
                    default=Value(0, output_field=DecimalField()),
                    output_field=DecimalField()
                ),

                # ===== Pending Logic =====
                pending_amount=ExpressionWrapper(
                    Cast(F('security_amount'), DecimalField(max_digits=10, decimal_places=2)) -
                    Coalesce(
                        Subquery(tailor_refund_subquery),
                        Value(0, output_field=DecimalField())
                    ) -
                    Coalesce(
                        Subquery(tailor_deduction_subquery),
                        Value(0, output_field=DecimalField())
                    ),
                    output_field=DecimalField()
                ),

                # ===== Payment Method =====
                payment_method_field=Coalesce(
                    Subquery(tailor_payment_method_subquery),
                    Value('-')
                ),
                tailor_security_account_id=Subquery(tailor_security_account_id_subquery),
            ).values(
                'id',
                'shop_tailor_id',
                'return_date',
                'security_amount',
                'refunded_amount',
                'deducted_amount',
                'pending_amount',
                'client_name_field',
                'client_phone_field',
                'payment_method_field',
                'tailor_security_account_id',
            )

            if client_id:
                tailor_security = tailor_security.filter(client_id=client_id)

            for security in tailor_security:
                transactions.append({
                    'id': security['id'],
                    'shop_tailor_id': security['shop_tailor_id'] or '',
                    'date': format_datetime_with_ampm(security['return_date']),
                    'sort_date': security['return_date'],
                    'type': 'security',
                    'name': security['client_name_field'],
                    'client_phone': security['client_phone_field'] or '-',
                    'amount': safe_decimal(security['security_amount']),
                    'refunded_amount': safe_decimal(security['refunded_amount']),
                    'deducted_amount': safe_decimal(security['deducted_amount']),
                    'pending_amount': safe_decimal(security['pending_amount']),
                    'payment_method': security['payment_method_field'],
                    'account_id': security['tailor_security_account_id'],
                    'account_name': account_map.get(security['tailor_security_account_id']),
                })

        # ============ TAILOR ORDERS ============
        if 'all' in txn_types or 'tailor_orders' in txn_types:
            tailor_order_qs = TailorOrder.objects.filter(
                shop_id=shop_id,
                created_at__date__gte=from_date,
                created_at__date__lte=to_date,
                deleted__isnull=True,
            ).select_related('client').annotate(
                client_name_field=Coalesce('client__client_name', Value('Customer')),
                client_phone_field=Coalesce(
                    Cast('client__phone_1_e164', CharField()),
                    Cast('client__client_phone_1', CharField()),
                    Value('')
                )
            ).values(
                'id', 'shop_tailor_id', 'created_at', 'client_name_field', 'client_phone_field',
                'customer_total_charge', 'paid_amount', 'security_amount', 'customer_order_type'
            )

            if client_id:
                tailor_order_qs = tailor_order_qs.filter(client_id=client_id)

            tailor_orders_list = list(tailor_order_qs)
            order_ids = [o['id'] for o in tailor_orders_list]

            tailor_items_map = {}
            if order_ids:
                for item in (
                    TailorOrderItem.objects
                    .filter(order_id__in=order_ids, deleted__isnull=True)
                    .values('order_id', 'product_name')
                ):
                    if item['product_name']:
                        tailor_items_map.setdefault(item['order_id'], []).append(item['product_name'])

            for order in tailor_orders_list:
                items = ', '.join(tailor_items_map.get(order['id'], [])) or 'No Items'
                paid = safe_decimal(order['paid_amount'])
                security = safe_decimal(order['security_amount'])
                transactions.append({
                    'id': order['id'],
                    'shop_tailor_id': order['shop_tailor_id'] or '',
                    'date': format_date(order['created_at']),
                    'sort_date': order['created_at'],
                    'type': 'tailor_orders',
                    'customer_order_type': order['customer_order_type'],
                    'reference_id': order['id'],
                    'invoice_id': order['shop_tailor_id'] or '',
                    'name': order['client_name_field'],
                    'client_phone': order['client_phone_field'] or '-',
                    'paid_amount': paid,
                    'paid_amount_with_security': paid + security,
                    'items': items,
                    'created_at': format_date(order['created_at']),
                })

        # Separate pending and non-pending transactions
        pending_txns = [t for t in transactions if t.get('type') == 'pendings']
        other_txns = [t for t in transactions if t.get('type') != 'pendings']

        # Helper to normalize sort_date to datetime for comparison
        def get_sort_datetime(txn):
            sort_date = txn.get('sort_date')
            if sort_date is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            # Convert date to datetime if needed
            if hasattr(sort_date, 'hour'):
                return sort_date
            else:  # It's a date, convert to timezone-aware datetime
                naive_dt = datetime.combine(sort_date, datetime.min.time())
                return timezone.make_aware(naive_dt)

        # Sort non-pending transactions by date (newest first)
        other_txns.sort(key=get_sort_datetime, reverse=True)

        # Sort pending transactions by date (oldest first)
        pending_txns.sort(key=get_sort_datetime)

        # Combine: non-pending first, then pending
        final_transactions = other_txns + pending_txns

        return final_transactions