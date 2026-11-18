import logging
from collections import OrderedDict, defaultdict
from datetime import datetime

from django.db.models import F
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from booking_v2.models import Client
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from sales.models import Sale, SalesPaymentDetails
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import FeatureRolePermissionFactory

from ..utils.date_utils import ensure_ist
from .pagination import DayGroupedPagination

logger = logging.getLogger('api_requests')


class SaleDetailAmountAPIView(StandardResponseMixin, APIView):
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        # SubscriptionActivePermission,
        # FeatureRolePermissionFactory("SALES", allowed_roles=["OWNER", "MANAGER", "STAFF"]),
    ]

    @extend_schema(
        tags=["Sales Management V4"],
        summary="Get wallet sales summary",
        description="Returns grouped day-wise wallet summary combining sales and completed tailor orders with optional client filter.",
        parameters=[
            OpenApiParameter(name="client_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Optional client id filter."),
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated grouped wallet summary response."),
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
            OpenApiExample("Request example", value={"client_id": 22, "page": 1}, request_only=True),
            OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": {"May 2026": {"04-05-2026": {"total_expense": 3800, "items": [{"id": 10, "invoice_id": "SAL-10", "total_amount": 2400, "items": "Sherwani, Blazer", "payment_methods": "Cash", "created_at": "04-05-2026 11:20:10", "type": "sale"}]}}}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Invalid client response", value={"status": "validation_error", "message": "Client ID must be a valid integer.", "data": {"validation_errors": {"client_id": ["Client ID must be a valid integer."]}}}, response_only=True, status_codes=["400"]),
            OpenApiExample("Server error response", value={"status": "error", "message": "An error occurred while fetching sale details.", "data": None}, response_only=True, status_codes=["500"]),
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

            # Validate client if provided
            client = None
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

            # -------- Optimized Sales Query --------
            sales_query = Sale.objects.filter(
                shop_id=shop_id
            ).select_related("client").prefetch_related(
                "items__product_variant__product",
                "payments"
            ).annotate(
                item_id=F('id'),
                invoice_id_field=F('shop_sale_id'),
                client_name_field=F('client__client_name'),
                total_amount_field=F('total_amount') - F('discount'),
                date_field=F('sale_date'),
                created_at_field=F('created_at')
            ).values(
                'item_id',
                'invoice_id_field',
                'client_name_field',
                'total_amount_field',
                'date_field',
                'created_at_field'
            ).order_by('-sale_date', '-created_at')

            if client:
                sales_query = sales_query.filter(client=client)

            # -------- Optimized Tailor Orders Query --------
            from tailored.models import TailorOrder

            tailor_query = TailorOrder.objects.filter(
                shop_id=shop_id,
                customer_order_type='sale',
                status='completed',
                deleted__isnull=True
            ).select_related("client").prefetch_related(
                "items",
                "payments"
            ).annotate(
                item_id=F('id'),
                invoice_id_field=F('shop_tailor_id'),
                client_name_field=F('client__client_name'),
                total_amount_field=F('customer_total_charge') - F('discount_amount'),
                date_field=F('completed_at'),
                created_at_field=F('created_at')
            ).values(
                'item_id',
                'invoice_id_field',
                'client_name_field',
                'total_amount_field',
                'date_field',
                'created_at_field'
            ).order_by('-completed_at', '-created_at')

            if client:
                tailor_query = tailor_query.filter(client=client)

            # -------- Process and combine data --------
            combined_data = []

            # Process sales data
            sales_ids = [item['item_id'] for item in sales_query]
            sales_objects = {
                sale.id: sale for sale in Sale.objects.filter(
                    id__in=sales_ids
                ).select_related("client").prefetch_related(
                    "items__product_variant__product",
                    "payments"
                )
            }

            for item in sales_query:
                sale_obj = sales_objects.get(item['item_id'])
                if sale_obj:
                    items = [item.product_variant.product.name for item in sale_obj.items.all()]
                    items_str = ", ".join(items) if items else "No Items"

                    first_payment = sale_obj.payments.first()
                    payment_method = ""
                    if first_payment:
                        payment_method = dict(SalesPaymentDetails.PAYMENT_METHOD_CHOICES).get(
                            first_payment.payment_method,
                            first_payment.payment_method
                        )

                    date_field = item['date_field']
                    created_at_field = item['created_at_field']

                    date_str = date_field.strftime("%d-%m-%Y") if date_field else None
                    created_at_str = None
                    if created_at_field:
                        local_created_at = ensure_ist(created_at_field)
                        created_at_str = local_created_at.strftime("%d-%m-%Y %H:%M:%S")

                    combined_data.append({
                        "id": item['item_id'],
                        "invoice_id": item['invoice_id_field'] or str(item['item_id']),
                        "total_amount": int(item['total_amount_field']) if item['total_amount_field'] else 0,
                        "items": items_str,
                        "payment_methods": payment_method,
                        "created_at": created_at_str,
                        "type": "sale",
                        "date_for_grouping": date_field,
                        "sort_date": date_field or created_at_field.date() if created_at_field else None
                    })

            # Process tailor orders data
            tailor_ids = [item['item_id'] for item in tailor_query]
            tailor_objects = {
                order.id: order for order in TailorOrder.objects.filter(
                    id__in=tailor_ids
                ).select_related("client").prefetch_related("items", "payments")
            }

            for item in tailor_query:
                tailor_obj = tailor_objects.get(item['item_id'])
                if tailor_obj:
                    items = [item.product_name for item in tailor_obj.items.all()]
                    items_str = ", ".join(items) if items else "No Items"

                    first_payment = tailor_obj.payments.first()
                    payment_method = ""
                    if first_payment:
                        from tailored.models import CustomerPayment
                        payment_method = dict(CustomerPayment.PAYMENT_METHOD_CHOICES).get(
                            first_payment.payment_method,
                            first_payment.payment_method
                        )

                    date_field = item['date_field']
                    created_at_field = item['created_at_field']

                    grouping_date = date_field.date() if date_field else (created_at_field.date() if created_at_field else None)
                    date_str = grouping_date.strftime("%d-%m-%Y") if grouping_date else None

                    created_at_str = None
                    if created_at_field:
                        local_created_at = ensure_ist(created_at_field)
                        created_at_str = local_created_at.strftime("%d-%m-%Y %H:%M:%S")

                    combined_data.append({
                        "id": item['item_id'],
                        "invoice_id": item['invoice_id_field'] or f"TO{item['item_id']:06d}",
                        "total_amount": int(item['total_amount_field']) if item['total_amount_field'] else 0,
                        "items": items_str,
                        "payment_methods": payment_method,
                        "created_at": created_at_str,
                        "type": "tailor_order",
                        "date_for_grouping": grouping_date,
                        "sort_date": grouping_date
                    })

            # -------- Optimized grouping by date --------
            grouped_by_date = defaultdict(list)

            for item in combined_data:
                if item["date_for_grouping"]:
                    grouping_date = item["date_for_grouping"]
                    month_year = grouping_date.strftime("%B %Y")
                    day = grouping_date.strftime("%d-%m-%Y")

                    # Remove internal fields from final output
                    final_item = {k: v for k, v in item.items()
                                  if k not in ["date_for_grouping", "sort_date"]}

                    grouped_by_date[f"{month_year}|{day}"].append(final_item)

            # -------- Sort and structure data with totals --------
            def parse_date_key(date_str):
                try:
                    return datetime.strptime(date_str, "%d-%m-%Y").date()
                except ValueError:
                    return datetime.min.date()

            structured_data = OrderedDict()
            for key, items in grouped_by_date.items():
                month_year, day = key.split("|")

                sorted_items = sorted(items, key=lambda x: x["total_amount"], reverse=True)
                day_total = sum(item["total_amount"] for item in sorted_items)

                if month_year not in structured_data:
                    structured_data[month_year] = OrderedDict()

                structured_data[month_year][day] = {
                    "total_expense": day_total,
                    "items": sorted_items
                }

            # Sort days within each month (most recent first)
            for month_year in structured_data:
                sorted_days = OrderedDict(
                    sorted(
                        structured_data[month_year].items(),
                        key=lambda x: parse_date_key(x[0]),
                        reverse=True
                    )
                )
                structured_data[month_year] = sorted_days

            # Sort months (most recent first)
            def parse_month_key(month_str):
                try:
                    return datetime.strptime(month_str, "%B %Y")
                except ValueError:
                    return datetime.min

            structured_data = OrderedDict(
                sorted(
                    structured_data.items(),
                    key=lambda x: parse_month_key(x[0]),
                    reverse=True
                )
            )

            # -------- Pagination --------
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

        except Exception as e:
            logger.error("Error occurred while fetching sale details.", exc_info=True)
            return self.error_response(
                message="An error occurred while fetching sale details.",
                dev_message=str(e)
            )
