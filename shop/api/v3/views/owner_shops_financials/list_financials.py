from rest_framework.decorators import action
from shop.models import ShopUser
from decimal import Decimal
from datetime import datetime
import calendar
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["Shop Management V3"],
    summary="Get owner shops financial summary",
    description="Returns payment-based revenue/expense/profit summaries for shops where current user is OWNER.",
    parameters=[
        OpenApiParameter("period", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="YYYY or YYYY-MM."),
        OpenApiParameter("shop_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Optional specific shop id."),
    ],
    responses={
        200: OpenApiResponse(description="Standard success response with shops and overall financial metrics."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Invalid month in period.\n"
                "- Invalid period format.\n"
                "- Invalid year in period.\n"
                "- Invalid shop ID provided.\n"
                "- Month must be between 1 and 12\n"
                "- Format must be YYYY-MM (e.g., 2025-01) or YYYY\n"
                "- Year must be a valid integer\n"
                "- Shop ID must be a valid integer"
            )
        ),
        500: OpenApiResponse(description="Exact message: Unable to fetch financials right now. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"period": "2026-05", "shop_id": 4}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Financial summary fetched successfully.", "data": {"shops": [{"shop_id": 4, "shop_name": "Prime Tailors", "totals": {"revenue": 250000.0, "expense": 140000.0, "profit": 110000.0}}], "overall": {"revenue": 250000.0, "expense": 140000.0, "profit": 110000.0}}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="owner-shops-financials")
def list_financials(self, request):
    try:
        # Parse query parameters
        period_q = request.query_params.get("period", "").strip()
        shop_id_q = request.query_params.get("shop_id")

        # Determine year and month filter from period parameter
        year = datetime.now().year
        month_filter = None

        if period_q:
            if "-" in period_q:
                # Monthly format: "YYYY-MM"
                try:
                    year_str, month_str = period_q.split("-")
                    year = int(year_str)
                    month_filter = int(month_str)
                    if not (1 <= month_filter <= 12):
                        return self.validation_error_response(
                            errors={"period": ["Month must be between 1 and 12"]},
                            message="Invalid month in period."
                        )
                except (ValueError, IndexError):
                    return self.validation_error_response(
                        errors={"period": ["Format must be YYYY-MM (e.g., 2025-01) or YYYY"]},
                        message="Invalid period format."
                    )
            else:
                # Yearly format: "YYYY"
                try:
                    year = int(period_q)
                except ValueError:
                    return self.validation_error_response(
                        errors={"period": ["Year must be a valid integer"]},
                        message="Invalid year in period."
                    )

        # Find shops where user is OWNER and active link
        shop_query = ShopUser.objects.filter(
            user=self.user,
            role="OWNER",
            is_active=True
        ).select_related("shop")

        # Filter by specific shop if provided
        if shop_id_q:
            try:
                shop_id_filter = int(shop_id_q)
                shop_query = shop_query.filter(shop_id=shop_id_filter)
            except (ValueError, TypeError):
                return self.validation_error_response(
                    errors={"shop_id": ["Shop ID must be a valid integer"]},
                    message="Invalid shop ID provided."
                )

        shop_users = list(shop_query)
        shop_ids = [su.shop_id for su in shop_users]
        shop_details = {su.shop_id: su.shop for su in shop_users if su.shop}

        if not shop_ids:
            return self.success_response(
                data={
                    "shops": [],
                    "overall": {
                        "revenue": 0, "expense": 0, "profit": 0,
                        "revenue_growth": 0, "expense_growth": 0, "profit_growth": 0
                    }
                },
                message="No owned shops found for this user.",
                dev_message="User has no ShopUser role OWNER records."
            )

        # Determine which months to process
        months = [month_filter] if month_filter else list(range(1, 13))
        month_names = {m: calendar.month_name[m] for m in range(1, 13)}

        # Get current period data
        revenue_map = self._get_payment_based_revenue(shop_ids, year, months)
        expenses_data, pve_data = self._get_monthly_expense_data(shop_ids, year, months)

        # Get previous period data for growth calculation
        prev_revenue_map = self._get_previous_period_revenue(shop_ids, year, month_filter)
        prev_expense_map = self._get_previous_period_expense(shop_ids, year, month_filter)
        prev_revenue_breakdown = self._get_previous_period_revenue_breakdown(shop_ids, year, month_filter) if month_filter else {}

        # Build expense map
        expense_map = {}
        for item in expenses_data:
            key = (item["shop_id"], item["month"])
            expense_type = (item["type"] or "").strip().lower()
            type_key = "salary" if "salary" in expense_type else (expense_type or "other")
            amount = Decimal(str(item["total"]))

            if key not in expense_map:
                expense_map[key] = {"total": Decimal("0.00"), "by_type": {}}

            expense_map[key]["total"] += amount
            expense_map[key]["by_type"][type_key] = expense_map[key]["by_type"].get(type_key, Decimal("0.00")) + amount

        for item in pve_data:
            key = (item["shop_id"], item["month"])
            expense_type = (item["type"] or "").strip().lower()
            type_key = "salary" if "salary" in expense_type else (expense_type or "other")
            amount = Decimal(str(item["total"]))

            if key not in expense_map:
                expense_map[key] = {"total": Decimal("0.00"), "by_type": {}}

            expense_map[key]["total"] += amount
            expense_map[key]["by_type"][type_key] = expense_map[key]["by_type"].get(type_key, Decimal("0.00")) + amount

        # Build response data
        shops_list = []
        overall_current = {"revenue": Decimal("0.00"), "expense": Decimal("0.00"), "profit": Decimal("0.00")}
        overall_previous = {"revenue": Decimal("0.00"), "expense": Decimal("0.00"), "profit": Decimal("0.00")}

        # Get previous month data for monthly growth calculation
        monthly_prev_revenue = {}
        monthly_prev_expense = {}
        if month_filter:
            monthly_prev_revenue = self._get_previous_period_revenue(shop_ids, year, month_filter)
            monthly_prev_expense = self._get_previous_period_expense(shop_ids, year, month_filter)

        for shop_id in shop_ids:
            shop_obj = shop_details.get(shop_id)
            if not shop_obj:
                continue

            shop_months = []
            shop_current = {"revenue": Decimal("0.00"), "expense": Decimal("0.00"), "profit": Decimal("0.00")}

            # Get previous period data for this shop
            prev_revenue = prev_revenue_map.get(shop_id, Decimal("0.00"))
            prev_expense = prev_expense_map.get(shop_id, Decimal("0.00"))
            prev_profit = prev_revenue - prev_expense

            overall_previous["revenue"] += prev_revenue
            overall_previous["expense"] += prev_expense
            overall_previous["profit"] += prev_profit

            # For yearly view, track previous month profit for sequential growth
            prev_month_profit = None
            prev_month_bookings = None
            prev_month_sales = None

            for month in months:
                revenue_data = revenue_map.get((shop_id, month), {
                    'bookings_amount': Decimal("0.00"),
                    'bookings_ids': set(),
                    'sales_amount': Decimal("0.00"),
                    'sales_ids': set(),
                    'tailor_amount': Decimal("0.00"),
                    'tailor_ids': set()
                })

                expense_data = expense_map.get((shop_id, month), {
                    "total": Decimal("0.00"),
                    "by_type": {}
                })

                bookings_earnings = revenue_data['bookings_amount']
                sales_earnings = revenue_data['sales_amount'] + revenue_data['tailor_amount']
                total_revenue = bookings_earnings + sales_earnings
                total_expense = expense_data["total"]
                profit = total_revenue - total_expense

                # Calculate growth percentage
                if month_filter:
                    # For single month view, compare with previous month
                    prev_month_revenue = monthly_prev_revenue.get(shop_id, Decimal("0.00"))
                    prev_month_expense = monthly_prev_expense.get(shop_id, Decimal("0.00"))
                    prev_month_profit = prev_month_revenue - prev_month_expense

                    revenue_growth = self._calculate_growth_percentage(total_revenue, prev_month_revenue)
                    expense_growth = self._calculate_growth_percentage(total_expense, prev_month_expense)
                    profit_growth = self._calculate_growth_percentage(profit, prev_month_profit)

                    prev_b = prev_revenue_breakdown.get(shop_id, {}).get('bookings', Decimal("0.00"))
                    prev_s = prev_revenue_breakdown.get(shop_id, {}).get('sales', Decimal("0.00"))
                    bookings_growth = self._calculate_growth_percentage(bookings_earnings, prev_b)
                    sales_growth = self._calculate_growth_percentage(sales_earnings, prev_s)
                else:
                    # For yearly view, calculate month-over-month growth
                    profit_growth = 0.0
                    if prev_month_profit is not None and prev_month_profit != Decimal("0.00"):
                        try:
                            profit_growth = float(round(((profit - prev_month_profit) / prev_month_profit) * Decimal("100.00"), 2))
                        except Exception:
                            profit_growth = 0.0
                    prev_month_profit = profit

                    bookings_growth = 0.0
                    if prev_month_bookings is not None:
                        bookings_growth = self._calculate_growth_percentage(bookings_earnings, prev_month_bookings)
                    sales_growth = 0.0
                    if prev_month_sales is not None:
                        sales_growth = self._calculate_growth_percentage(sales_earnings, prev_month_sales)
                    prev_month_bookings = bookings_earnings
                    prev_month_sales = sales_earnings

                shop_months.append({
                    "month": month,
                    "month_name": month_names[month],
                    "bookings_count": len(revenue_data['bookings_ids']),
                    "bookings_earnings": float(round(bookings_earnings, 2)),
                    "bookings_growth": bookings_growth,
                    "sales_count": len(revenue_data['sales_ids']) + len(revenue_data['tailor_ids']),
                    "sales_earnings": float(round(sales_earnings, 2)),
                    "sales_growth": sales_growth,
                    "total_revenue": float(round(total_revenue, 2)),
                    "total_expense": float(round(total_expense, 2)),
                    "expense_breakdown": {k: float(round(v, 2)) for k, v in expense_data["by_type"].items()},
                    "profit": float(round(profit, 2)),
                    "growth": profit_growth
                })

                shop_current["revenue"] += total_revenue
                shop_current["expense"] += total_expense
                shop_current["profit"] += profit

            # Only include shop if it has activity
            if shop_months:
                # Calculate growth percentages for shop
                revenue_growth = self._calculate_growth_percentage(shop_current["revenue"], prev_revenue)
                expense_growth = self._calculate_growth_percentage(shop_current["expense"], prev_expense)
                profit_growth = self._calculate_growth_percentage(shop_current["profit"], prev_profit)

                shops_list.append({
                    "shop_id": shop_id,
                    "shop_name": shop_obj.name,
                    "shop_address": self._build_shop_address(shop_obj),
                    "months": shop_months,
                    "totals": {
                        "revenue": float(round(shop_current["revenue"], 2)),
                        "expense": float(round(shop_current["expense"], 2)),
                        "profit": float(round(shop_current["profit"], 2)),
                        "revenue_growth": revenue_growth,
                        "expense_growth": expense_growth,
                        "profit_growth": profit_growth
                    }
                })

                overall_current["revenue"] += shop_current["revenue"]
                overall_current["expense"] += shop_current["expense"]
                overall_current["profit"] += shop_current["profit"]

        # Calculate overall growth percentages
        overall_revenue_growth = self._calculate_growth_percentage(
            overall_current["revenue"], overall_previous["revenue"]
        )
        overall_expense_growth = self._calculate_growth_percentage(
            overall_current["expense"], overall_previous["expense"]
        )
        overall_profit_growth = self._calculate_growth_percentage(
            overall_current["profit"], overall_previous["profit"]
        )

        shops_list.sort(key=lambda x: x["totals"]["profit_growth"], reverse=True)

        overall_out = {
            "revenue": float(round(overall_current["revenue"], 2)),
            "expense": float(round(overall_current["expense"], 2)),
            "profit": float(round(overall_current["profit"], 2)),
            "revenue_growth": overall_revenue_growth,
            "expense_growth": overall_expense_growth,
            "profit_growth": overall_profit_growth
        }

        period_display = f"{year}-{month_filter:02d}" if month_filter else str(year)
        return self.success_response(
            data={"shops": shops_list, "overall": overall_out},
            message="Financial summary fetched successfully.",
            dev_message=f"Computed payment-based financials for {len(shops_list)} shop(s) for period {period_display}."
        )

    except Exception as e:
        logger.error(f"OwnerShopsFinancials error for user {getattr(self.user,'id',None)}: {e}")
        return self.error_response(
            message="Unable to fetch financials right now. Please try again later.",
            dev_message=str(e)
        )
