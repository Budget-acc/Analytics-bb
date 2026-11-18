from rest_framework.decorators import action
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

logs = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V4"],
    summary="Get staff monthly transactions (V4)",
    description=(
        "Returns detailed monthly transaction list (bookings, sales, tailor orders, or pending "
        "bookings) for a staff member.\n\n"
        "V4 extends V3 by adding support for transaction_type: \"tailor_orders\". "
        "Tailor orders included are completed orders that have not been converted into bookings.\n\n"
        "V4 also adds transaction_type: \"pending_bookings\", returning the same paginated "
        "booking-list structure as transaction_type=\"bookings\" (same row fields), filtered "
        "instead to bookings created during the requested month that are still "
        "booking_status=\"upcoming\" and not delivery_status=\"cancelled\".\n\n"
        "Rows are ordered earliest first: bookings and tailor orders by completed_at, sales by "
        "sale_date (ties by id), the same order as the earnings-excel report. pending_bookings "
        "stays newest created first."
    ),
    request=inline_serializer(
        name="ShopV4StaffMonthlyTransactionsRequest",
        fields={
            "staff_id": serializers.IntegerField(required=True),
            "month": serializers.IntegerField(required=True),
            "year": serializers.IntegerField(required=True),
            "transaction_type": serializers.CharField(
                required=True,
                help_text="bookings, sales, tailor_orders, or pending_bookings",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(
            description=(
                "Success response from monthly bookings/sales/tailor_orders/pending_bookings "
                "helpers. Standard pagination envelope under `data` for every transaction_type: "
                "next, previous, count, page_size, current_page, total_pages, start_index, "
                "end_index, and data (the list of rows for the requested transaction_type).\n\n"
                "transaction_type=\"pending_bookings\" returns the same row shape as "
                "transaction_type=\"bookings\" (id, shop_booking_id, client, pickup_date, "
                "return_date, cooling_period_end, booking_status, payment_status, staff_name, "
                "delivery_status, created_at, total_amount, discount_amount, earning, "
                "booked_items), filtered to bookings created during the requested month that are "
                "still booking_status=\"upcoming\" and not delivery_status=\"cancelled\"."
            )
        ),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Please provide valid request data.\n"
                "- Invalid data format.\n"
                "- Staff ID is required.\n"
                "- Month is required.\n"
                "- Month must be between 1 and 12.\n"
                "- Year is required.\n"
                "- Year must be between 2000 and 2100.\n"
                "- Transaction type is required.\n"
                "- Transaction type must be either \"bookings\", \"sales\", \"tailor_orders\", or "
                "\"pending_bookings\".\n"
                "- Invalid numeric values provided."
            )
        ),
        404: OpenApiResponse(description="Exact message: Staff member not found."),
        500: OpenApiResponse(description="Exact message: An unexpected error occurred while retrieving transactions."),
    },
    examples=[
        OpenApiExample(
            "Request example (tailor orders)",
            value={"staff_id": 10, "month": 5, "year": 2026, "transaction_type": "tailor_orders"},
            request_only=True,
        ),
        OpenApiExample(
            "Request example (bookings)",
            value={"staff_id": 10, "month": 5, "year": 2026, "transaction_type": "bookings"},
            request_only=True,
        ),
        OpenApiExample(
            "Success response (bookings)",
            value={
                "status": "success",
                "message": "Data retrieved successfully",
                "data": {
                    "next": None,
                    "previous": None,
                    "count": 1,
                    "page_size": 15,
                    "current_page": 1,
                    "total_pages": 1,
                    "start_index": 1,
                    "end_index": 1,
                    "data": [
                        {
                            "id": 501,
                            "shop_booking_id": "BK-501",
                            "client": "Jane Doe",
                            "pickup_date": "05-09-2025 10:00:00",
                            "return_date": "07-09-2025 10:00:00",
                            "cooling_period_end": None,
                            "booking_status": "completed",
                            "payment_status": True,
                            "staff_name": "John Doe",
                            "delivery_status": "delivered",
                            "created_at": "01-09-2025 09:12:00",
                            "total_amount": 3000,
                            "discount_amount": 0,
                            "earning": 3000,
                            "booked_items": ["Silk Saree"],
                        }
                    ],
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Request example (pending bookings)",
            value={"staff_id": 10, "month": 9, "year": 2025, "transaction_type": "pending_bookings"},
            request_only=True,
        ),
        OpenApiExample(
            "Success response (pending bookings)",
            value={
                "status": "success",
                "message": "Data retrieved successfully",
                "data": {
                    "next": None,
                    "previous": None,
                    "count": 2,
                    "page_size": 15,
                    "current_page": 1,
                    "total_pages": 1,
                    "start_index": 1,
                    "end_index": 2,
                    "data": [
                        {
                            "id": 1601,
                            "shop_booking_id": "000651",
                            "client": "Rahul Menon",
                            "pickup_date": "25-09-2026 10:00:00",
                            "return_date": "27-09-2026 10:00:00",
                            "cooling_period_end": None,
                            "booking_status": "upcoming",
                            "payment_status": False,
                            "staff_name": "Anshifs",
                            "delivery_status": "booked",
                            "created_at": "05-09-2026 14:22:10",
                            "total_amount": 3500,
                            "discount_amount": 0,
                            "earning": 3500,
                            "booked_items": ["Xuv 300 Vdi"],
                        }
                    ],
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Validation response",
            value={
                "status": "validation_error",
                "message": "Please provide valid request data.",
                "errors": {"month": ["Month must be between 1 and 12."]},
            },
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
@action(detail=False, methods=['post'], url_path='monthly-transactions')
def get_monthly_transactions(self, request):
    """
    Get detailed bookings, sales, tailor_orders list, or pending_bookings summary for a
    specific staff, month, and year.

    Request Body:
    {
        "staff_id": 6,
        "month": 9,
        "year": 2025,
        "transaction_type": "bookings" | "sales" | "tailor_orders" | "pending_bookings"
    }
    """
    try:
        staff_id = request.data.get('staff_id')
        month = request.data.get('month')
        year = request.data.get('year')
        transaction_type = request.data.get('transaction_type')

        validation_errors = {}

        if not staff_id:
            validation_errors['staff_id'] = ['Staff ID is required.']

        if not month:
            validation_errors['month'] = ['Month is required.']
        elif not (1 <= int(month) <= 12):
            validation_errors['month'] = ['Month must be between 1 and 12.']

        if not year:
            validation_errors['year'] = ['Year is required.']
        elif not (2000 <= int(year) <= 2100):
            validation_errors['year'] = ['Year must be between 2000 and 2100.']

        if not transaction_type:
            validation_errors['transaction_type'] = ['Transaction type is required.']
        elif transaction_type not in ['bookings', 'sales', 'tailor_orders', 'pending_bookings']:
            validation_errors['transaction_type'] = [
                'Transaction type must be either "bookings", "sales", "tailor_orders", or "pending_bookings".'
            ]

        if validation_errors:
            return self.validation_error_response(
                errors=validation_errors,
                message='Please provide valid request data.',
                dev_message='Validation failed for monthly transactions request'
            )

        staff = self._validate_staff_exists(staff_id)
        if not staff:
            return self.not_found_response(
                resource='Staff',
                message='Staff member not found.',
                dev_message=f'Staff {staff_id} not found in shop {self.shop_id}'
            )

        if transaction_type == 'bookings':
            return self._get_monthly_bookings(staff_id, month, year)
        elif transaction_type == 'sales':
            return self._get_monthly_sales(staff_id, month, year)
        elif transaction_type == 'pending_bookings':
            return self._get_monthly_pending_bookings(staff_id, month, year)
        else:
            return self._get_monthly_tailor_orders(staff_id, month, year)

    except ValueError as e:
        return self.validation_error_response(
            errors={'data': ['Invalid numeric values provided.']},
            message='Invalid data format.',
            dev_message=f'ValueError in monthly transactions: {str(e)}'
        )

    except Exception as e:
        logs.error(f"Unexpected error in monthly transactions for staff {staff_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message='An unexpected error occurred while retrieving transactions.',
            dev_message=f'Unexpected error: {str(e)}'
        )
