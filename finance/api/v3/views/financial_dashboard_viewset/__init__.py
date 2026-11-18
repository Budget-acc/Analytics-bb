from .base import FinancialDashboardViewSetBase
from .retrieve import FinancialDashboardRetrieveMixin
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

class FinancialDashboardViewSet(FinancialDashboardRetrieveMixin, FinancialDashboardViewSetBase):
    """
    Consolidated financial dashboard endpoint for finance app.
    Supports sections: bookings, pending, sales, security.
    """
    @extend_schema(
        tags=["Finance Management V3"],
        summary="Get financial dashboard",
        description=(
            "Returns consolidated finance dashboard data for one section at a time. "
            "Sections: bookings, pending, sales, security, custom_work."
        ),
        parameters=[
            OpenApiParameter(name="sections", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, default="bookings", description="Single section key. One of: bookings, pending, sales, security, custom_work."),
            OpenApiParameter(name="client_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional client filter."),
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated section-wise dashboard response."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Client ID must be a valid integer.\n"
                    "- Client not found for this shop.\n"
                    "- Invalid section '{raw}'. Valid options: ['bookings', 'custom_work', 'pending', 'sales', 'security']."
                )
            ),
            403: OpenApiResponse(
                description=(
                    "Permission errors. Exact message:\n"
                    "- Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(description="Exact message: Unable to retrieve dashboard data."),
        },
        examples=[
            OpenApiExample(
                "Request example - bookings section",
                description="GET /finance/v3/financial-dashboard/?sections=bookings&page=1",
                value={
                    "sections": "bookings",
                    "page": 1,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "count": 24,
                    "next": "https://api.example.com/finance/v3/financial-dashboard/?sections=bookings&page=2",
                    "previous": None,
                    "results": [
                        {
                            "id": 223,
                            "client_name": "Rahul",
                            "total_amount": "3500.00",
                            "paid_amount": "1500.00",
                            "pending_amount": "2000.00",
                        }
                    ],
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid input response",
                value={
                    "status": "error",
                    "message": "Client ID must be a valid integer.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Invalid section response",
                value={
                    "status": "error",
                    "message": "Invalid section 'foo'. Valid options: ['bookings', 'custom_work', 'pending', 'sales', 'security'].",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Permission denied response",
                value={
                    "status": "permission_denied",
                    "message": "Access to this shop has been deactivated.",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to retrieve dashboard data.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

__all__ = ["FinancialDashboardViewSet"]
