from rest_framework.decorators import action
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

logs = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="Get staff monthly transactions",
    description="Returns detailed monthly transaction list (bookings or sales) for a staff member.",
    request=inline_serializer(
        name="ShopV3StaffMonthlyTransactionsRequest",
        fields={
            "staff_id": serializers.IntegerField(required=True),
            "month": serializers.IntegerField(required=True),
            "year": serializers.IntegerField(required=True),
            "transaction_type": serializers.CharField(required=True, help_text="bookings or sales"),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response from monthly bookings/sales helpers."),
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
                "- Transaction type must be either \"bookings\" or \"sales\".\n"
                "- Invalid numeric values provided."
            )
        ),
        404: OpenApiResponse(description="Exact message: Staff member not found."),
        500: OpenApiResponse(description="Exact message: An unexpected error occurred while retrieving transactions."),
    },
    examples=[
        OpenApiExample("Request example", value={"staff_id": 10, "month": 5, "year": 2026, "transaction_type": "bookings"}, request_only=True),
        OpenApiExample("Validation response", value={"status": "validation_error", "message": "Please provide valid request data.", "errors": {"month": ["Month must be between 1 and 12."]}}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=['post'], url_path='monthly-transactions')
def get_monthly_transactions(self, request):
    """
    Get detailed bookings or sales list for a specific staff, month, and year

    Request Body:
    {
        "staff_id": 6,
        "month": 9,
        "year": 2025,
        "transaction_type": "bookings" | "sales"
    }
    """
    try:
        # Validate required fields
        staff_id = request.data.get('staff_id')
        month = request.data.get('month')
        year = request.data.get('year')
        transaction_type = request.data.get('transaction_type')

        # Validation
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
        elif transaction_type not in ['bookings', 'sales']:
            validation_errors['transaction_type'] = ['Transaction type must be either "bookings" or "sales".']

        if validation_errors:
            return self.validation_error_response(
                errors=validation_errors,
                message='Please provide valid request data.',
                dev_message='Validation failed for monthly transactions request'
            )

        # Validate staff exists
        staff = self._validate_staff_exists(staff_id)
        if not staff:
            return self.not_found_response(
                resource='Staff',
                message='Staff member not found.',
                dev_message=f'Staff {staff_id} not found in shop {self.shop_id}'
            )

        # Get transactions based on type
        if transaction_type == 'bookings':
            return self._get_monthly_bookings(staff_id, month, year)
        else:
            return self._get_monthly_sales(staff_id, month, year)

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
