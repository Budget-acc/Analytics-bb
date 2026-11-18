from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from tailored.models import Tailor
from tailored.api.v3.utils.tailor_payment_summary import get_tailor_completed_items
from django.utils.timezone import localtime
from ERP.utils.custom_pagination import StandardPageNumberPagination
from datetime import datetime
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Tailor monthly summary",
    description=(
        "Returns paginated, month-grouped tailor order items (any status) for a given tailor in the active shop. "
        "If year is omitted, orders from all years are returned (grouped by month across years)."
    ),
    parameters=[
        OpenApiParameter("tailor_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor id."),
        OpenApiParameter("year", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Optional. Defaults to all years if omitted. Valid range when provided: 2020 to 2030."),
        OpenApiParameter("month", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Optional month filter from 1 to 12."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated monthly summary response."),
        400: OpenApiResponse(
            description=(
                "Exact error messages:\n"
                "- Invalid tailor ID provided.\n"
                "- Year must be between 2020 and 2030.\n"
                "- Year must be a valid number.\n"
                "- Month must be between 1 and 12.\n"
                "- Month must be a valid number."
            )
        ),
        404: OpenApiResponse(description="Exact error message: Tailor not found."),
        500: OpenApiResponse(description="Exact error message: Unable to fetch monthly summary. Please try again."),
    },
    examples=[
        OpenApiExample("Monthly summary success", value={"count": 1, "next": None, "previous": None, "results": {"October 2025": {"total": 2500.0, "tailor_orders": [{"id": 7, "shop_tailor_id": "TO-0007", "expected_completion_date": "15-10-2025 12:00:00", "product_name": "Sherwani", "tailor_cost": 2500.0}]}}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=['get'], url_path=r'tailor-monthly-summary/(?P<tailor_id>\d+)')
def tailor_monthly_summary(self, request, tailor_id=None):
    """
    Get monthly summary of tailor orders (any status) for a specific tailor.

    URL: /tailor-monthly-summary/{tailor_id}/

    Query Parameters:
    - year: Filter by specific year (optional; defaults to all years if omitted)
    - month: Filter by specific month (1-12, optional)

    Returns orders grouped by month with totals and pagination support.

    Data source logic:
    - Items with TailorOrderItemAssignment rows → fetched from the assignment table (new path).
    - Items with NO assignment rows (legacy) → fetched from TailorOrderItem.assigned_tailor (old path).
    """

    # Validate tailor_id parameter
    try:
        tailor_id = int(tailor_id)
    except (ValueError, TypeError):
        return self.validation_error_response(
            message="Invalid tailor ID provided.",
            dev_message=f"tailor_id is not a valid integer: {tailor_id}"
        )

    # Validate query parameters
    year = request.query_params.get('year')
    month = request.query_params.get('month')

    # Validate year parameter
    if year:
        try:
            year = int(year)
            if year < 2020 or year > 2030:
                return self.validation_error_response(
                    message="Year must be between 2020 and 2030.",
                    dev_message=f"Invalid year: {year}"
                )
        except (ValueError, TypeError):
            return self.validation_error_response(
                message="Year must be a valid number.",
                dev_message=f"Invalid year format: {year}"
            )
    else:
        year = None  # No year filter - return orders across all years

    # Validate month parameter
    if month:
        try:
            month = int(month)
            if month < 1 or month > 12:
                return self.validation_error_response(
                    message="Month must be between 1 and 12.",
                    dev_message=f"Invalid month: {month}"
                )
        except (ValueError, TypeError):
            return self.validation_error_response(
                message="Month must be a valid number.",
                dev_message=f"Invalid month format: {month}"
            )

    try:
        # Validate tailor belongs to current shop
        if not Tailor.objects.filter(
            id=tailor_id,
            shop_id=self.shop_id,
            is_deleted=False
        ).exists():
            return self.not_found_response(
                resource="Tailor",
                message="Tailor not found.",
                dev_message=f"Tailor {tailor_id} not found in shop {self.shop_id} or is deleted"
            )

        # Fetch tailor-charge items of any status from both sources (assignment +
        # legacy) via the shared helper - no status filtering is applied.
        # year/month are only applied when explicitly provided.
        items = get_tailor_completed_items(tailor_id, self.shop_id, year=year, month=month)

        # Sort combined list newest first
        items.sort(key=lambda x: x['expected_completion_date'], reverse=True)

        # Apply pagination
        paginator = StandardPageNumberPagination()
        paginated_items = paginator.paginate_queryset(items, request)

        # Group by month and calculate totals
        from collections import defaultdict
        import calendar

        monthly_data = defaultdict(lambda: {
            'total': 0,
            'tailor_orders': []
        })

        for item in paginated_items:
            completion_date = item['expected_completion_date']
            if completion_date:
                local_dt = localtime(completion_date)
                formatted_date = local_dt.strftime("%d-%m-%Y %H:%M:%S")
                month_name = calendar.month_name[local_dt.month]
                month_key = f"{month_name} {local_dt.year}"
                tailor_charge = float(item['tailor_charge'])
                monthly_data[month_key]['total'] += tailor_charge
                monthly_data[month_key]['tailor_orders'].append({
                    'id': item['tailor_order_id'],
                    'shop_tailor_id': item['shop_tailor_id'],
                    'expected_completion_date': formatted_date,
                    'product_name': item['product_name'],
                    'tailor_cost': tailor_charge,
                })

        # Sort months chronologically (most recent first)
        def sort_month_key(month_str):
            try:
                return datetime.strptime(month_str, "%B %Y")
            except ValueError:
                return datetime.min

        sorted_monthly_data = {}
        for month_key in sorted(monthly_data.keys(), key=sort_month_key, reverse=True):
            monthly_data[month_key]['tailor_orders'].sort(
                key=lambda x: datetime.strptime(x['expected_completion_date'], "%d-%m-%Y %H:%M:%S"),
                reverse=True
            )
            sorted_monthly_data[month_key] = monthly_data[month_key]

        return paginator.get_paginated_response(sorted_monthly_data)

    except Exception as e:
        logger.error(f"Error fetching monthly summary for tailor {tailor_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to fetch monthly summary. Please try again.",
            dev_message=f"Monthly summary fetch failed: {str(e)}"
        )
