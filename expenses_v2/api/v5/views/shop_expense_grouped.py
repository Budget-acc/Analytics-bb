import logging
from collections import defaultdict, OrderedDict
from datetime import datetime, date

from django.db.models import (
    F, Value, IntegerField, BooleanField, CharField, Case, When, DateField,
)
from django.db.models.functions import Coalesce
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from booking_v2.models import RefundDetails, SecurityDepositAdjustment
from expenses_v2.api.v4.views.pagination import DayGroupedPagination
from expenses_v2.models import Expenses, ProductVariantExpense
from shop.models import ShopUser
from shop.api.v3.utils.shop import get_active_shop_id
from tailored.models import TailorOrderRefund, TailorOrderSecurityDepositAdjustment, TailorPayment
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class ShopExpenseGroupedAPIViewV5(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_owner_name(self, shop_id):
        return (
            ShopUser.objects
            .select_related("user")
            .filter(shop_id=shop_id, role="OWNER", is_active=True)
            .values_list("user__full_name", flat=True)
            .first()
        )

    def get_expenses_data(self, shop_id, owner_name):
        # General expenses
        general_expenses = (
            Expenses.objects
            .filter(shop_id=shop_id)
            .exclude(expense_date_time__isnull=True)
            .select_related("user")
            .annotate(
                variant_id=Value(None, output_field=IntegerField()),
                product_id=Value(None, output_field=IntegerField()),
                service_id=Value(None, output_field=IntegerField()),
                user_name=Coalesce("user__full_name", Value(owner_name, output_field=CharField())),
                is_deleted=Value(False, output_field=BooleanField()),
                source=Value('expense', output_field=CharField()),
                reference_id=Value(None, output_field=IntegerField()),
                entity_name=Value(None, output_field=CharField()),
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
                "source", "reference_id", "entity_name",
            )
        )

        # Product variant expenses
        variant_expenses = (
            ProductVariantExpense.objects
            .filter(shop_id=shop_id)
            .exclude(expense_date_time__isnull=True)
            .select_related(
                "linked_expense__user",
                "user",
                "product_variant__product__shop_service"
            )
            .annotate(
                variant_id=F("product_variant_id"),
                product_id=F("product_variant__product_id"),
                service_id=F("product_variant__product__shop_service_id"),
                user_name=Case(
                    When(linked_expense__user__full_name__isnull=False,
                         then=F("linked_expense__user__full_name")),
                    When(user__full_name__isnull=False,
                         then=F("user__full_name")),
                    default=Value(owner_name, output_field=CharField()),
                    output_field=CharField(),
                ),
                is_deleted=Case(
                    When(product_variant__deleted__isnull=False, then=Value(True, output_field=BooleanField())),
                    When(product_variant__product__deleted__isnull=False, then=Value(True, output_field=BooleanField())),
                    When(product_variant__isnull=True, then=Value(True, output_field=BooleanField())),
                    When(product_variant__product__isnull=True, then=Value(True, output_field=BooleanField())),
                    default=Value(False, output_field=BooleanField()),
                    output_field=BooleanField(),
                ),
                source=Value('product_variant_expense', output_field=CharField()),
                reference_id=Value(None, output_field=IntegerField()),
                entity_name=Value(None, output_field=CharField()),
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
                "source", "reference_id", "entity_name",
            )
        )

        # Booking refunds
        booking_refunds = (
            RefundDetails.objects
            .filter(booking__shop_id=shop_id, booking__deleted__isnull=True)
            .select_related("refunded_by", "booking")
            .annotate(
                variant_id=Value(None, output_field=IntegerField()),
                product_id=Value(None, output_field=IntegerField()),
                service_id=Value(None, output_field=IntegerField()),
                user_name=Coalesce(
                    "refunded_by__full_name",
                    Value(owner_name, output_field=CharField()),
                ),
                is_deleted=Value(False, output_field=BooleanField()),
                expense=F("refunded_amount"),
                type=F("booking__shop_booking_id"),
                description=F("refund_reason"),
                payment_method=F("refund_method"),
                expense_date_time=F("created_at"),
                date=Value(None, output_field=DateField()),
                source=Value('booking_refund', output_field=CharField()),
                reference_id=F("booking_id"),
                entity_name=Value(None, output_field=CharField()),
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
                "source", "reference_id", "entity_name",
            )
        )

        # Tailor order refunds
        tailor_refunds = (
            TailorOrderRefund.objects
            .filter(order__shop_id=shop_id, order__created_booking__isnull=True)
            .select_related("refunded_by", "order")
            .annotate(
                variant_id=Value(None, output_field=IntegerField()),
                product_id=Value(None, output_field=IntegerField()),
                service_id=Value(None, output_field=IntegerField()),
                user_name=Coalesce(
                    "refunded_by__full_name",
                    Value(owner_name, output_field=CharField()),
                ),
                is_deleted=Value(False, output_field=BooleanField()),
                expense=F("refunded_amount"),
                type=F("order__shop_tailor_id"),
                description=F("refund_reason"),
                payment_method=F("refund_method"),
                expense_date_time=F("created_at"),
                date=Value(None, output_field=DateField()),
                source=Value('tailored_order_refund', output_field=CharField()),
                reference_id=F("order_id"),
                entity_name=Value(None, output_field=CharField()),
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
                "source", "reference_id", "entity_name",
            )
        )

        # Booking security deposit refunds
        booking_security_refunds = (
            SecurityDepositAdjustment.objects
            .filter(
                booking__shop_id=shop_id,
                booking__deleted__isnull=True,
                adjustment_type='refund',
            )
            .select_related("booking")
            .annotate(
                variant_id=Value(None, output_field=IntegerField()),
                product_id=Value(None, output_field=IntegerField()),
                service_id=Value(None, output_field=IntegerField()),
                user_name=Value(owner_name, output_field=CharField()),
                is_deleted=Value(False, output_field=BooleanField()),
                expense=F("amount"),
                type=F("booking__shop_booking_id"),
                description=F("reason"),
                expense_date_time=F("created_at"),
                date=Value(None, output_field=DateField()),
                source=Value('booking_security_refund', output_field=CharField()),
                reference_id=F("booking_id"),
                entity_name=Value(None, output_field=CharField()),
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
                "source", "reference_id", "entity_name",
            )
        )

        # Tailor order security deposit refunds
        tailor_security_refunds = (
            TailorOrderSecurityDepositAdjustment.objects
            .filter(
                order__shop_id=shop_id,
                order__deleted__isnull=True,
                order__created_booking__isnull=True,
                adjustment_type='refunded',
            )
            .select_related("order")
            .annotate(
                variant_id=Value(None, output_field=IntegerField()),
                product_id=Value(None, output_field=IntegerField()),
                service_id=Value(None, output_field=IntegerField()),
                user_name=Value(owner_name, output_field=CharField()),
                is_deleted=Value(False, output_field=BooleanField()),
                expense=F("amount"),
                type=F("order__shop_tailor_id"),
                description=F("reason"),
                expense_date_time=F("created_at"),
                date=Value(None, output_field=DateField()),
                source=Value('tailor_order_security_refund', output_field=CharField()),
                reference_id=F("order_id"),
                entity_name=Value(None, output_field=CharField()),
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
                "source", "reference_id", "entity_name",
            )
        )

        # Tailor payments (money paid out to tailors)
        tailor_payments = (
            TailorPayment.objects
            .filter(shop_id=shop_id)
            .select_related("created_by", "category", "tailor")
            .annotate(
                variant_id=Value(None, output_field=IntegerField()),
                product_id=Value(None, output_field=IntegerField()),
                service_id=Value(None, output_field=IntegerField()),
                user_name=Coalesce("created_by__full_name", Value(owner_name, output_field=CharField())),
                is_deleted=Value(False, output_field=BooleanField()),
                expense=F("amount"),
                type=F("category__name"),
                description=F("notes"),
                expense_date_time=F("transaction_date_time"),
                date=Value(None, output_field=DateField()),
                source=Value('tailor_payment', output_field=CharField()),
                reference_id=F("tailor_id"),
                entity_name=F("tailor__name"),
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
                "source", "reference_id", "entity_name",
            )
        )

        return (
            list(general_expenses)
            + list(variant_expenses)
            + list(booking_refunds)
            + list(tailor_refunds)
            + list(booking_security_refunds)
            + list(tailor_security_refunds)
            + list(tailor_payments)
        )

    def group_expenses_by_date(self, expenses_data):
        grouped_by_date = defaultdict(list)

        for exp in expenses_data:
            expense_dt = localtime(exp["expense_date_time"]) if exp.get("expense_date_time") else None
            if not expense_dt:
                continue

            expense_date = expense_dt.date()

            if not expense_date:
                continue

            if isinstance(expense_date, date):
                normalized_date = expense_date
            else:
                normalized_date = expense_date.date()

            date_key = normalized_date.strftime("%d-%m-%Y")
            month_key = normalized_date.strftime("%B %Y")
            day_key = f"{month_key}|{date_key}"

            grouped_by_date[day_key].append({
                "id": exp["id"],
                "variant_id": exp["variant_id"],
                "product_id": exp["product_id"],
                "service_id": exp["service_id"],
                "expense": exp["expense"],
                "type": exp["type"],
                "description": exp["description"],
                "user": exp["user_name"],
                "created_at": expense_dt.strftime("%d-%m-%Y %H:%M:%S"),
                "is_deleted": exp["is_deleted"],
                "payment_method": exp.get("payment_method") or None,
                "account_id": exp.get("account_id") or None,
                "source": exp.get("source"),
                "reference_id": exp.get("reference_id"),
                "entity_name": exp.get("entity_name"),
            })

        return grouped_by_date

    def sort_and_structure_data(self, grouped_by_date):
        def get_date_from_key(day_key):
            _, date_str = day_key.split("|")
            return datetime.strptime(date_str, "%d-%m-%Y")

        sorted_day_keys = sorted(
            grouped_by_date.keys(),
            key=get_date_from_key,
            reverse=True
        )

        structured_data = OrderedDict()
        for day_key in sorted_day_keys:
            day_items = grouped_by_date[day_key]

            day_items.sort(
                key=lambda x: datetime.strptime(x["created_at"], "%d-%m-%Y %H:%M:%S"),
                reverse=True
            )

            _security_sources = {"booking_security_refund", "tailor_order_security_refund"}

            # Single-pass split: security vs non-security items
            security_items = []
            non_security_items = []
            for item in day_items:
                if item.get("source") in _security_sources:
                    security_items.append(item)
                else:
                    non_security_items.append(item)

            # expenses: non-security items only (security is shown separately)
            day_total  = sum(item["expense"] for item in non_security_items)
            cash_total = sum(
                item["expense"] for item in non_security_items
                if (item.get("payment_method") or "").lower() == "cash"
            )
            upi_total = day_total - cash_total

            # security_refund breakdown
            total_security_refund = sum(item["expense"] for item in security_items)
            security_cash = sum(
                item["expense"] for item in security_items
                if (item.get("payment_method") or "").lower() == "cash"
            )
            security_upi = total_security_refund - security_cash

            month_key, date_key = day_key.split("|")

            structured_data[day_key] = {
                "month": month_key,
                "date": date_key,
                "total_expense": day_total,
                "expenses": {
                    "upi": upi_total,
                    "cash": cash_total,
                    "total": day_total,
                },
                "total_security_refund": total_security_refund,
                "security_refund": {
                    "upi": security_upi,
                    "cash": security_cash,
                    "total": total_security_refund,
                },
                "items": day_items
            }

        return structured_data

    @extend_schema(
        tags=["Expense Management V5"],
        summary="Get grouped paid expenses (V5)",
        description=(
            "Returns wallet expenses grouped by Month → Day, combining seven sources: "
            "general expenses, product variant expenses, booking refunds, tailored order refunds, "
            "booking security refunds, tailor order security refunds, and tailor payments. "
            "Each day group includes per-day totals and a cash/UPI payment-method split. "
            "Each item carries 'source' (expense | product_variant_expense | booking_refund | tailored_order_refund | booking_security_refund | tailor_order_security_refund | tailor_payment) "
            "and 'reference_id' (null for expenses, booking_id/order_id for refunds, tailor_id for tailor payments). "
            "'entity_name' is a generic, reusable field present on every item: it holds the name of "
            "the related entity for that expense source (currently the tailor's name for "
            "'tailor_payment' items; null for all other sources today, reserved for future sources "
            "to populate with their own related entity name, e.g. vendor/staff/client). "
            "Results are paginated in groups of 6 days per page."
        ),
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Day-group page number. Default: 1.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Grouped day-wise expense response.\n\n"
                    "Shape: { '<Month YYYY>': { '<DD-MM-YYYY>': { "
                    "'total_expense': Decimal (security refunds excluded), "
                    "'expenses': { 'upi': Decimal, 'cash': Decimal, 'total': Decimal } "
                    "(security refunds excluded), "
                    "'total_security_refund': Decimal, "
                    "'security_refund': { 'upi': Decimal, 'cash': Decimal, 'total': Decimal }, "
                    "'items': [ { 'id', 'variant_id', 'product_id', 'service_id', 'expense', "
                    "'type', 'description', 'user', 'created_at', 'is_deleted', "
                    "'payment_method', 'account_id', 'source', 'reference_id', "
                    "'entity_name' (generic related-entity name field, present on every item; "
                    "currently the tailor's name for source='tailor_payment', null otherwise) } ] } } }\n\n"
                    "Pagination metadata: count, total_pages, current_page, next, previous."
                )
            ),
            403: OpenApiResponse(
                description="Exact message: Your access to this shop has been deactivated."
            ),
            500: OpenApiResponse(
                description="Exact message: Something went wrong while fetching shop expenses."
            ),
        },
    )
    def get(self, request):
        try:
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated.",
                )

            owner_name = self.get_owner_name(shop_id)
            expenses_data = self.get_expenses_data(shop_id, owner_name)
            grouped_by_date = self.group_expenses_by_date(expenses_data)
            structured_data = self.sort_and_structure_data(grouped_by_date)

            paginator = DayGroupedPagination()
            paginated_data = paginator.paginate_day_groups(structured_data, request)

            if paginated_data is None:
                return paginator.get_paginated_response(OrderedDict())

            final_response = OrderedDict()
            for day_key, day_data in paginated_data.items():
                month = day_data["month"]
                date = day_data["date"]

                if month not in final_response:
                    final_response[month] = OrderedDict()

                final_response[month][date] = {
                    "total_expense": day_data["total_expense"],
                    "expenses": day_data["expenses"],
                    "total_security_refund": day_data.get("total_security_refund", 0),
                    "security_refund": day_data.get("security_refund", {"upi": 0, "cash": 0, "total": 0}),
                    "items": day_data["items"]
                }

            return paginator.get_paginated_response(final_response)

        except Exception as e:
            logger.error(f"ShopExpenseGroupedAPIViewV5 error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching shop expenses.",
                dev_message=str(e)
            )
