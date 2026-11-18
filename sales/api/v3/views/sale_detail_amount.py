import logging
from collections import OrderedDict, defaultdict

import pytz
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from booking_v2.models import Client
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from sales.models import Sale, SalesPaymentDetails
from shop.api.v3.utils.shop import get_active_shop_id

from .pagination import DayGroupedPagination

logger = logging.getLogger('api_requests')

IST = pytz.timezone("Asia/Kolkata")


def ensure_ist(dt):
    """Convert datetime to IST timezone"""
    if timezone.is_naive(dt):
        # Treat naive datetimes as UTC first
        dt = timezone.make_aware(dt, timezone.utc)
    return dt.astimezone(IST)


class SaleDetailAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Get wallet sales summary",
        description="Returns month/day grouped sales totals and item-level entries, optionally filtered by client.",
        parameters=[
            OpenApiParameter(name="client_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional client filter for wallet sales summary."),
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number for grouped-day pagination."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated month/day grouped sales summary response."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Client ID must be a valid integer.\n"
                    "- Client not found for this shop."
                )
            ),
            403: OpenApiResponse(description="Exact message: Your access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: An error occurred while fetching sale details."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "client_id": 22,
                    "page": 1,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": {
                        "May 2026": {
                            "04-05-2026": {
                                "total_expense": "2400.00",
                                "items": [
                                    {
                                        "id": 120,
                                        "sale_date": "04-05-2026",
                                        "total_amount": "2400.00",
                                        "client_name": "Rahul",
                                        "items": ["Sherwani", "Blazer"],
                                        "payment_methods": ["Cash"],
                                        "created_at": "04-05-2026 11:42:12"
                                    }
                                ]
                            }
                        }
                    }
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid client id response",
                value={
                    "status": "validation_error",
                    "message": "Client ID must be a valid integer.",
                    "data": {
                        "validation_errors": {
                            "client_id": ["Client ID must be a valid integer."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Client not found response",
                value={
                    "status": "validation_error",
                    "message": "Client not found for this shop.",
                    "data": {
                        "validation_errors": {
                            "client_id": ["Client not found for this shop."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Permission denied response",
                value={
                    "status": "permission_denied",
                    "message": "Your access to this shop has been deactivated.",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "An error occurred while fetching sale details.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def get(self, request):
        try:
            try:
                shop_id = get_active_shop_id(request)
            except Exception:
                return self.permission_denied_response(
                    message="Your access to this shop has been deactivated."
                )

            client_id = request.query_params.get("client_id")
            sales_qs = Sale.objects.filter(shop_id=shop_id)

            if client_id:
                if not client_id.isdigit():
                    return self.validation_error_response(
                        errors={"client_id": ["Client ID must be a valid integer."]},
                        message="Client ID must be a valid integer."
                    )
                try:
                    client = Client.objects.get(id=client_id, shop_id=shop_id)
                except Client.DoesNotExist:
                    return self.validation_error_response(
                        errors={"client_id": ["Client not found for this shop."]},
                        message="Client not found for this shop."
                    )
                sales_qs = sales_qs.filter(client=client)

            sales_qs = (
                sales_qs
                .select_related("client")
                .prefetch_related("items", "payments")
                .order_by("-sale_date", "-created_at")
            )

            # Group sales
            grouped_by_date = defaultdict(list)

            for sale in sales_qs:
                local_sale_date = sale.sale_date
                month_year = local_sale_date.strftime("%B %Y")
                day = local_sale_date.strftime("%d-%m-%Y")

                local_created_at = ensure_ist(sale.created_at)

                items = [item.product_variant.product.name for item in sale.items.all()]
                payment_methods = [p.payment_method for p in sale.payments.all()]
                payment_methods_display = [
                    dict(SalesPaymentDetails.PAYMENT_METHOD_CHOICES).get(pm, pm)
                    for pm in payment_methods
                ]

                grouped_by_date[f"{month_year}|{day}"].append({
                    "id": sale.id,
                    "sale_date": local_sale_date.strftime("%d-%m-%Y"),
                    "total_amount": sale.total_amount - sale.discount,
                    "client_name": sale.client.client_name if sale.client else None,
                    "items": items,
                    "payment_methods": payment_methods_display,
                    "created_at": local_created_at.strftime("%d-%m-%Y %H:%M:%S"),
                })

            # Structure data with totals
            structured_data = OrderedDict()
            for key, sales in grouped_by_date.items():
                month_year, day = key.split("|")
                day_total = sum(s["total_amount"] for s in sales)

                if month_year not in structured_data:
                    structured_data[month_year] = OrderedDict()

                structured_data[month_year][day] = {
                    "total_expense": day_total,
                    "items": sales
                }

            # Flatten for pagination
            flat_group_by_day = OrderedDict()
            for month_year, days in structured_data.items():
                for day, data in days.items():
                    flat_group_by_day[f"{month_year}|{day}"] = data

            paginator = DayGroupedPagination()
            paginated_group = paginator.paginate_day_grouped(flat_group_by_day, request)

            # Rebuild final grouped structure
            final_group = OrderedDict()
            if isinstance(paginated_group, OrderedDict):
                for key, data in paginated_group.items():
                    month_year, day = key.split("|")
                    if month_year not in final_group:
                        final_group[month_year] = OrderedDict()
                    final_group[month_year][day] = data
            else:
                final_group = structured_data

            return paginator.get_paginated_response(final_group)

        except Exception:
            logging.error("Error occurred while fetching sale details.", exc_info=True)
            return self.error_response(
                message="An error occurred while fetching sale details."
            )
