from rest_framework.decorators import action
from django.db import DatabaseError, OperationalError
import calendar
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logs = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="Get staff earnings report",
    description="Returns monthly earnings summary for one staff member including bookings, sales, totals, and performance percentage.",
    parameters=[
        OpenApiParameter("staff_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Staff id."),
        OpenApiParameter("year", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Optional target year."),
        OpenApiParameter("month", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Optional target month."),
    ],
    responses={
        200: OpenApiResponse(description="Standard success response with earnings metrics."),
        404: OpenApiResponse(description="Exact message: Staff member not found."),
        500: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- An unexpected error occurred. Please try again later.\n"
                "- An unexpected error occurred. Please contact support.\n"
                "- An unexpected error occurred while generating the report."
            )
        ),
    },
    examples=[
        OpenApiExample("Success response", value={"status": "success", "message": "Staff earnings report retrieved successfully.", "data": {"staff_id": 10, "month": 5, "year": 2026, "total_earning": 12500.0, "performance_percent": 8.5}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=['get'], url_path='earnings-report/(?P<staff_id>[^/.]+)')
def get_earnings_report(self, request, staff_id=None):
    try:
        staff_id = int(staff_id)
        staff = self._validate_staff_exists(staff_id)
        if not staff:
            return self.not_found_response(
                resource='Staff',
                message='Staff member not found.',
                dev_message=f'Staff {staff_id} not found in shop {self.shop_id}'
            )

        year, month = self._get_target_year_month(request)

        # Get bookings & sales
        bookings_data = self._get_staff_bookings_data(staff_id, year, month)
        sales_data = self._get_staff_sales_data(staff_id, year, month)

        # Compute previous month/year
        if month == 1:
            prev_month = 12
            prev_year = year - 1
        else:
            prev_month = month - 1
            prev_year = year

        # Get bookings & sales for previous month
        prev_bookings = self._get_staff_bookings_data(staff_id, prev_year, prev_month)
        prev_sales = self._get_staff_sales_data(staff_id, prev_year, prev_month)

        current_bookings_earned = bookings_data.get("total_bookings_earned_amount") or 0
        current_sales_earning = sales_data.get("total_sales_earning") or 0
        total_current = current_bookings_earned + current_sales_earning

        prev_bookings_earned = prev_bookings.get("total_bookings_earned_amount") or 0
        prev_sales_earning = prev_sales.get("total_sales_earning") or 0
        total_prev = prev_bookings_earned + prev_sales_earning

        # Performance percentage: positive for growth, negative for loss.
        if total_prev == 0:
            performance_percent = 0
        else:
            performance_percent = round(((total_current - total_prev) / total_prev) * 100, 2)

        # total_earning = (
        #     bookings_data["total_bookings_earned_amount"] +
        #     sales_data["total_sales_earning"]
        # )

        response_data = {
            "staff_id": staff_id,
            "staff_name": staff.name,
            "year": year,
            "month": month,
            "month_name": calendar.month_name[month],
            "total_bookings_count": bookings_data.get("total_bookings_count", 0) or 0,
            "total_bookings_pending_count": bookings_data.get("total_bookings_pending_count", 0) or 0,
            "total_bookings_pending_amount": bookings_data.get("total_bookings_pending_amount", 0) or 0,
            "total_bookings_earned_count": bookings_data.get("total_bookings_earned_count", 0) or 0,
            "total_bookings_earned_amount": bookings_data.get("total_bookings_earned_amount", 0) or 0,
            "total_sales_count": sales_data.get("total_sales_count", 0) or 0,
            "total_sales_earning": sales_data.get("total_sales_earning", 0) or 0,
            "total_earning": (bookings_data.get("total_bookings_earned_amount", 0) or 0) +
                            (sales_data.get("total_sales_earning", 0) or 0),
            "performance_percent": performance_percent
        }

        return self.success_response(
            data=response_data,
            message="Staff earnings report retrieved successfully.",
            dev_message=f"Generated earnings report for staff {staff_id} ({staff.name}) for {month}-{year}"
        )

    except OperationalError as e:
        logs.error(f"Operational error in earnings report for staff {staff_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message='An unexpected error occurred. Please try again later.',
            dev_message=f'OperationalError: {str(e)}'
        )

    except DatabaseError as e:
        logs.error(f"Database error in earnings report for staff {staff_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message='An unexpected error occurred. Please contact support.',
            dev_message=f'DatabaseError: {str(e)}'
        )

    except Exception as e:
        logs.error(f"Unexpected error in earnings report for staff {staff_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="An unexpected error occurred while generating the report.",
            dev_message=f"Unexpected error: {str(e)}"
        )
