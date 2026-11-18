from rest_framework.decorators import action
from rest_framework import status
from shop.models import Shop
from booking_v2.models import BookingV2
from sales.models import Sale
from tailored.models import TailorOrder
from django.db.models import Count
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncYear
from datetime import datetime, timedelta, date
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Generate shop activity report",
    description="Generates grouped sales/bookings/tailor_orders counts by daily/weekly/monthly/yearly period within date range.",
    request=inline_serializer(
        name="ShopAdminV1GenerateReportRequest",
        fields={
            "shop_id": serializers.IntegerField(required=True),
            "report_type": serializers.CharField(required=False, allow_blank=True, help_text="sales | bookings | tailor_orders"),
            "period": serializers.CharField(required=False, default="daily", help_text="daily | weekly | monthly | yearly"),
            "start_date": serializers.CharField(required=True, help_text="YYYY-MM-DD or DD-MM-YYYY"),
            "end_date": serializers.CharField(required=True, help_text="YYYY-MM-DD or DD-MM-YYYY"),
        },
    ),
    responses={
        200: OpenApiResponse(description="Standard success response with report payload."),
        204: OpenApiResponse(description="No content response from no_content helper when report set is empty."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Shop ID is required.\n"
                "- Shop does not exist.\n"
                "- Date range is required.\n"
                "- Invalid date format.\n"
                "- Invalid report type.\n"
                "- Invalid period.\n"
                "- This field is required.\n"
                "- Invalid shop ID.\n"
                "- Both start_date and end_date are required.\n"
                "- Dates must be in YYYY-MM-DD or DD-MM-YYYY format.\n"
                "- Invalid. Must be 'sales', 'bookings', or 'tailor_orders'."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_id": 4, "report_type": "sales", "period": "monthly", "start_date": "2026-01-01", "end_date": "2026-05-01"}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Monthly report generated successfully", "data": {"shop_id": 4, "period": "monthly", "results": {"sales": [{"period": "January 2026", "count": 12}]}}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["post"], url_path="report")
def generate_report(self, request):
    shop_id = request.data.get("shop_id")
    report_type = request.data.get("report_type")
    period = request.data.get("period", "daily")
    start_date_raw = request.data.get("start_date")
    end_date_raw = request.data.get("end_date")

    # Validate shop_id
    if not shop_id:
        return self.validation_error_response(
            errors={"shop_id": ["This field is required."]},
            message="Shop ID is required."
        )

    try:
        shop = Shop.objects.get(id=shop_id)
    except Shop.DoesNotExist:
        return self.validation_error_response(
            errors={"shop_id": ["Invalid shop ID."]},
            message="Shop does not exist."
        )

    # Validate and parse dates
    if not start_date_raw or not end_date_raw:
        return self.validation_error_response(
            errors={"start_date/end_date": ["Both start_date and end_date are required."]},
            message="Date range is required."
        )

    def parse_flexible_date(date_str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    start_date = parse_flexible_date(start_date_raw)
    end_date = parse_flexible_date(end_date_raw)

    if start_date is None or end_date is None:
        return self.validation_error_response(
            errors={"date_format": ["Dates must be in YYYY-MM-DD or DD-MM-YYYY format."]},
            message="Invalid date format."
        )

    if report_type and report_type not in ["sales", "bookings", "tailor_orders"]:
        return self.validation_error_response(
            errors={"report_type": ["Invalid. Must be 'sales', 'bookings', or 'tailor_orders'."]},
            message="Invalid report type."
        )

    if period not in ["daily", "weekly", "monthly", "yearly"]:
        return self.validation_error_response(
            errors={"period": ["Invalid period."]},
            message="Invalid period."
        )

    trunc_map = {
        "daily": TruncDay("created_at"),
        "weekly": TruncWeek("created_at"),  # Sunday start
        "monthly": TruncMonth("created_at"),
        "yearly": TruncYear("created_at"),
    }

    def generate_period_range(period, start, end):
        """
        Generates all date/week/month/year periods between start and end inclusive.
        Handles edge cases safely and ensures alignment.
        """
        if start > end:
            return []

        periods = []
        current = start

        if period == "daily":
            while current <= end:
                periods.append(current)
                current += timedelta(days=1)

        elif period == "weekly":
            # Align current to the nearest previous Sunday (Python weekday: 0=Mon, 6=Sun)
            current -= timedelta(days=current.weekday())
            while current <= end:
                periods.append(current)
                current += timedelta(weeks=1)

        elif period == "monthly":
            current = date(current.year, current.month, 1)
            while current <= end:
                periods.append(current)
                if current.month == 12:
                    current = date(current.year + 1, 1, 1)
                else:
                    current = date(current.year, current.month + 1, 1)

        elif period == "yearly":
            current = date(current.year, 1, 1)
            while current.year <= end.year:
                periods.append(current)
                current = date(current.year + 1, 1, 1)

        return periods

    def get_report(queryset):
        trunc_func = trunc_map[period]
        qs = (
            queryset.filter(shop_id=shop_id, created_at__date__gte=start_date, created_at__date__lte=end_date)
            .annotate(period=trunc_func)
            .values("period")
            .annotate(count=Count("id"))
            .order_by("period")
        )

        # Convert to dict {period_date: count}
        actual_data = {
            (entry["period"].date() if hasattr(entry["period"], "date") else entry["period"]): entry["count"]
            for entry in qs
        }

        # Generate full period list and fill missing with 0
        results = []
        for p in generate_period_range(period, start_date, end_date):
            count = actual_data.get(p, 0)

            if period == "weekly":
                week_start = p
                week_end = week_start + timedelta(days=6)
                if week_end > end_date:
                    week_end = end_date
                results.append({
                    "period": f"{week_start.strftime('%d %b')} - {week_end.strftime('%d %b')}",
                    "count": count
                })

            elif period == "monthly":
                results.append({
                    "period": p.strftime("%B %Y"),
                    "count": count
                })

            elif period == "yearly":
                results.append({
                    "period": str(p.year),
                    "count": count
                })

            else:  # daily
                results.append({
                    "period": p.strftime("%Y-%m-%d"),
                    "count": count
                })

        return results

    # Build report
    data = {}
    if not report_type or report_type == "sales":
        data["sales"] = get_report(Sale.objects.all())

    if not report_type or report_type == "bookings":
        data["bookings"] = get_report(BookingV2.objects.all())

    if not report_type or report_type == "tailor_orders":
        data["tailor_orders"] = get_report(TailorOrder.objects.all())

    if not data:
        return self.no_content(message=f"No {report_type or 'sales/bookings/tailor_orders'} data available.")

    return self.success_response(
        data={
            "shop_id": shop_id,
            "period": period,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "results": data
        },
        message=f"{period.capitalize()} report generated successfully"
    )
