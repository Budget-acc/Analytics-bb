import logging
from collections import defaultdict, OrderedDict
from datetime import datetime, date

from django.db.models import (
    F, Value, IntegerField, BooleanField, CharField, Case, When,
)
from django.db.models.functions import Coalesce
from django.utils.timezone import localtime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from expenses_v2.models import Expenses, ProductVariantExpense
from shop.models import ShopUser
from shop.api.v3.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from .pagination import DayGroupedPagination

logger = logging.getLogger("api_requests")


class ShopExpenseGroupedAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_owner_name(self, shop_id):
        """Get owner name efficiently"""
        owner = (
            ShopUser.objects
            .select_related("user")
            .filter(shop_id=shop_id, role="OWNER", is_active=True)
            .values_list("user__full_name", flat=True)
            .first()
        )
        return owner

    def get_expenses_data(self, shop_id, owner_name):
        """
        Fetch and combine expenses data with optimized queries
        """
        # Optimized query for general expenses
        general_expenses = (
            Expenses.objects
            .filter(shop_id=shop_id)
            .exclude(expense_date_time__isnull=True)
            .select_related("user")
            # .annotate(
            #     variant_id=Value(None),
            #     product_id=Value(None),
            #     service_id=Value(None),
            #     user_name=Coalesce("user__full_name", Value(owner_name)),
            #     is_deleted=Value(False)
            # )
            .annotate(
                variant_id=Value(None, output_field=IntegerField()),
                product_id=Value(None, output_field=IntegerField()),
                service_id=Value(None, output_field=IntegerField()),
                user_name=Coalesce("user__full_name", Value(owner_name, output_field=CharField())),
                is_deleted=Value(False, output_field=BooleanField())
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id"
            )
        )

        # Optimized query for variant expenses
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
                    When(product_variant__isnull=True, then=Value(True, output_field=BooleanField())),
                    When(product_variant__product__isnull=True, then=Value(True, output_field=BooleanField())),
                    default=Value(False, output_field=BooleanField()),
                    output_field=BooleanField(),
                )
            )
            .values(
                "id", "variant_id", "product_id", "service_id",
                "date", "expense_date_time", "created_at", "user_name", "expense",
                "type", "description", "is_deleted", "payment_method", "account_id",
            )
        )

        # Combine querysets efficiently
        all_expenses = list(general_expenses) + list(variant_expenses)
        return all_expenses

    def group_expenses_by_date(self, expenses_data):
        """
        Group expenses by date with optimized processing
        """
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

            # Create composite key for unique day identification
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
            })

        return grouped_by_date

    def sort_and_structure_data(self, grouped_by_date):
        """
        Sort data by date and structure with totals
        """
        # Sort by date (newest first)
        def get_date_from_key(day_key):
            _, date_str = day_key.split("|")
            return datetime.strptime(date_str, "%d-%m-%Y")

        sorted_day_keys = sorted(
            grouped_by_date.keys(),
            key=get_date_from_key,
            reverse=True
        )

        # Structure final data with day totals
        structured_data = OrderedDict()
        for day_key in sorted_day_keys:
            day_items = grouped_by_date[day_key]

            # Sort items within day by created_at (newest first)
            day_items.sort(
                key=lambda x: datetime.strptime(x["created_at"], "%d-%m-%Y %H:%M:%S"),
                reverse=True
            )

            # Calculate day total and payment-method breakdown
            day_total = sum(item["expense"] for item in day_items)
            cash_total = sum(
                item["expense"] for item in day_items
                if (item.get("payment_method") or "").lower() == "cash"
            )
            upi_total = day_total - cash_total

            # Split the composite key back to month and date
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
                "items": day_items
            }

        return structured_data

    @extend_schema(
        tags=["Expense Management V4"],
        summary="Get grouped paid expenses",
        description=(
            "Returns wallet expenses grouped by Month -> Day for paid expense entries. "
            "Combines general expenses and product variant expenses, includes per-day totals and payment-method split."
        ),
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Day-group page number. Optional. Default: 1.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Grouped day-wise paid expense response with pagination metadata."
            ),
            403: OpenApiResponse(
                description=(
                    "Permission error. Exact message:\n"
                    "- Your access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(
                description=(
                    "Server error. Exact message:\n"
                    "- Something went wrong while fetching shop expenses."
                )
            ),
        },
    )
    def get(self, request):
        try:
            # Get shop ID
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated.",
                )

            # Get owner name efficiently
            owner_name = self.get_owner_name(shop_id)

            # Get combined expenses data with optimized queries
            expenses_data = self.get_expenses_data(shop_id, owner_name)

            # Group by date
            grouped_by_date = self.group_expenses_by_date(expenses_data)

            # Sort and structure data
            structured_data = self.sort_and_structure_data(grouped_by_date)

            # Apply day-grouped pagination
            paginator = DayGroupedPagination()
            paginated_data = paginator.paginate_day_groups(structured_data, request)

            if paginated_data is None:
                # No data for this page
                return paginator.get_paginated_response(OrderedDict())

            # Group paginated data by month for final response
            final_response = OrderedDict()
            for day_key, day_data in paginated_data.items():
                month = day_data["month"]
                date = day_data["date"]

                if month not in final_response:
                    final_response[month] = OrderedDict()

                final_response[month][date] = {
                    "total_expense": day_data["total_expense"],
                    "expenses": day_data["expenses"],
                    "items": day_data["items"]
                }

            return paginator.get_paginated_response(final_response)

        except Exception as e:
            logger.error(f"ShopExpenseGroupedAPIView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while fetching shop expenses.",
                dev_message=str(e)
            )
