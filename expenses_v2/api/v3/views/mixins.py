from collections import namedtuple
from datetime import datetime

from django.db.models import Q, F, ExpressionWrapper, IntegerField, Prefetch
from django.utils.timezone import localtime, make_aware

from ERP.utils.mixins import StandardResponseMixin
from booking_v2.models import BookingV2, Client, PaymentDetailsV2
from expenses_v2.models import Expenses, ProductVariantExpense
from shop.models import ShopUser
from shop.api.v3.utils.shop import get_active_shop_id


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

            general_qs = Expenses.objects.filter(
                shop_id=shop_id
            ).filter(expense_date_filter).select_related("user")

            owner_user = (
                ShopUser.objects
                .filter(shop_id=shop_id, role="OWNER", is_active=True)
                .select_related("user")
                .first()
            )
            owner_name = owner_user.user.full_name if owner_user and owner_user.user else None

            for exp in general_qs:
                expense_sort_date = (
                    exp.expense_date_time if exp.expense_date_time
                    else make_aware(datetime.combine(exp.date, datetime.min.time())) if exp.date
                    else exp.created_at
                )
                display_date = (
                    localtime(exp.expense_date_time).strftime("%d-%m-%Y")
                    if exp.expense_date_time else exp.date.strftime("%d-%m-%Y")
                )
                transactions.append(Txn(
                    sort_date=expense_sort_date,
                    data={
                        "type": "expense",
                        "date": display_date,
                        "user": exp.user.full_name if exp.user else owner_name,
                        "description": exp.type or "General expense",
                        "amount": exp.expense,
                    }
                ))

            variant_qs = ProductVariantExpense.objects.filter(
                shop_id=shop_id
            ).filter(expense_date_filter).select_related("linked_expense__user", "product_variant__product")

            for exp in variant_qs:
                user = exp.linked_expense.user.full_name if exp.linked_expense and exp.linked_expense.user else owner_name
                product_name = exp.product_variant.product.name if exp.product_variant and exp.product_variant.product else ""
                expense_sort_date = (
                    exp.expense_date_time if exp.expense_date_time
                    else make_aware(datetime.combine(exp.date, datetime.min.time())) if exp.date
                    else exp.created_at
                )
                display_date = (
                    localtime(exp.expense_date_time).strftime("%d-%m-%Y")
                    if exp.expense_date_time else exp.date.strftime("%d-%m-%Y")
                )
                transactions.append(Txn(
                    sort_date=expense_sort_date,
                    data={
                        "type": "expense",
                        "date": display_date,
                        "user": user,
                        "description": exp.type or f"Product variant expense - {product_name}",
                        "amount": exp.expense,
                    }
                ))

        # ---------- PAYMENTS ----------
        if "all" in txn_types or "payments" in txn_types:
            payment_qs = PaymentDetailsV2.objects.filter(
                booking__shop_id=shop_id,
                booking__deleted__isnull=True,
                created_at__date__range=(from_date, to_date)
            ).select_related("booking__client_identity")

            if client_id:
                payment_qs = payment_qs.filter(booking__client_identity_id=client_id)

            for pay in payment_qs:
                client = pay.booking.client_identity
                local_dt = localtime(pay.created_at)
                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "payment",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "user": client.client_name if client else None,
                        "description": f"Payment via {pay.payment_method.title()}",
                        "amount": pay.payment_amount,
                    }
                ))

        # ---------- SECURITY TRANSACTIONS ----------
        bookings_qs = BookingV2.objects.filter(
            shop_id=shop_id, created_at__date__range=(from_date, to_date)
        ).select_related("client_identity")

        security_qs = bookings_qs.filter(security_amount__gt=0)

        if client_id:
            security_qs = security_qs.filter(client_identity_id=client_id)

        if "all" in txn_types or "security" in txn_types:
            for booking in security_qs:
                local_dt = localtime(booking.created_at)
                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "security",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "user": booking.client_identity.client_name if booking.client_identity else None,
                        "description": "Security collected",
                        "amount": booking.security_amount,
                    }
                ))

        # ---------- PENDING ----------
        bookings_qss = BookingV2.objects.filter(
            shop_id=shop_id, return_date__date__range=(from_date, to_date)
        ).select_related("client_identity")

        if "all" in txn_types or "pendings" in txn_types:
            pending_expression = ExpressionWrapper(
                F("total_amount") - F("discount_amount") - F("advance_amount"),
                output_field=IntegerField()
            )
            pending_qs = bookings_qss.annotate(pending_amount=pending_expression).filter(
                payment_status=False, pending_amount__gt=0
            )

            if client_id:
                pending_qs = pending_qs.filter(client_identity_id=client_id)

            for booking in pending_qs:
                local_dt = localtime(booking.return_date)
                pending_transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "pending",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "user": booking.client_identity.client_name if booking.client_identity else None,
                        "description": "Pending amount",
                        "amount": booking.pending_amount,
                    }
                ))

        # ---------- BOOKINGS ----------
        if "all" in txn_types or "bookings" in txn_types:
            bookings_qs = BookingV2.objects.filter(
                shop_id=shop_id,
                created_at__date__range=(from_date, to_date)
            ).select_related("client_identity")

            if client_id:
                bookings_qs = bookings_qs.filter(client_identity_id=client_id)

            for booking in bookings_qs:
                local_dt = localtime(booking.created_at)
                transactions.append(Txn(
                    sort_date=local_dt,
                    data={
                        "type": "booking",
                        "date": local_dt.strftime("%d-%m-%Y"),
                        "user": booking.client_identity.client_name if booking.client_identity else None,
                        "description": booking.shop_booking_id,
                        "amount": booking.total_amount,
                    }
                ))

        # ---------- SALES ----------
        if "all" in txn_types or "sales" in txn_types:
            from sales.models import Sale, SaleItem, SalesPaymentDetails
            sales_qs = (
                Sale.objects.filter(
                    shop_id=shop_id,
                    sale_date__range=(from_date, to_date)
                ).select_related("client")
                .prefetch_related(
                    Prefetch(
                        "items",
                        queryset=SaleItem.objects.select_related("product_variant__product")
                    ),
                    "payments"
                )
            )
            if client_id:
                sales_qs = sales_qs.filter(client_id=client_id)
            sales_qs = sales_qs.prefetch_related("items", "payments")
            for sale in sales_qs:
                sale_date = sale.sale_date.strftime("%d-%m-%Y")
                items = [item.product_variant.product.name for item in sale.items.all()]
                client_name = sale.client.client_name if sale.client else None
                transactions.append(Txn(
                    sort_date=sale.sale_date,
                    data={
                        "type": "sale",
                        "date": sale_date,
                        "user": client_name,
                        "description": sale.description or "Sale",
                        "amount": sale.total_amount - sale.discount,
                        "items": items,
                    }
                ))

        # Sort non-pending descending, pending ascending
        non_pending_sorted = sorted(transactions, key=lambda t: t.sort_date, reverse=True)
        pending_sorted = sorted(pending_transactions, key=lambda t: t.sort_date)
        final_transactions = non_pending_sorted + pending_sorted
        return [item.data for item in final_transactions]
