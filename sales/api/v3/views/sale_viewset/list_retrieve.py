import logging
from datetime import datetime

from django.db.models import Q
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from ERP.utils.custom_pagination import StandardPageNumberPagination
from sales.models import Sale
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation, skip_feature_validation

logger = logging.getLogger('api_requests')


class SaleListRetrieveMixin:

    # Allow viewing historical sales even if the shop's subscription has expired.
    @skip_subscription_validation
    @skip_feature_validation
    @extend_schema(
        tags=["Sales Management V3"],
        summary="List sales",
        description="Returns paginated sales for the active shop with optional search and date-range filters.",
        parameters=[
            OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Search by client name or shop sale id."),
            OpenApiParameter(name="from", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Start date filter. Formats: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="to", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="End date filter. Formats: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
            OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page size."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated sales list response."),
            403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Unable to retrieve sales at this time."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={
                    "search": "Rahul",
                    "from": "2026-05-01",
                    "to": "2026-05-31",
                    "page": 1,
                    "page_size": 15,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "count": 1,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 120,
                            "client_name": "Rahul",
                            "total_amount": "2500.00",
                            "paid_amount": "1200.00",
                            "discount": "100.00",
                            "sale_date": "04-05-2026",
                            "created_at": "04-05-2026 11:42:12",
                            "products": ["Sherwani", "Blazer"],
                        }
                    ],
                },
                response_only=True,
                status_codes=["200"],
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
                    "message": "Unable to retrieve sales at this time.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def list(self, request):
        """List all sales for the shop"""
        try:
            shop_id = self.shop_id
            sales = (
                Sale.objects.filter(shop_id=shop_id)
                .select_related("client")
                .prefetch_related(
                    "items__product_variant__product"
                )
                .order_by("-created_at")
            )

            search_query = request.query_params.get("search")
            if search_query:
                sales = sales.filter(
                    Q(client__client_name__icontains=search_query) |
                    Q(shop_sale_id__icontains=search_query)
                )

            # --- Date Range Filter ---
            date_from = request.query_params.get("from")
            date_to = request.query_params.get("to")

            def parse_date(date_str):
                if not date_str:
                    return None
                for fmt in ["%Y-%m-%d", "%d-%m-%Y"]:
                    try:
                        return datetime.strptime(date_str, fmt).date()
                    except ValueError:
                        continue
                return None

            parsed_from = parse_date(date_from)
            parsed_to = parse_date(date_to)

            if parsed_from:
                sales = sales.filter(sale_date__gte=parsed_from)
            if parsed_to:
                sales = sales.filter(sale_date__lte=parsed_to)

            paginator = StandardPageNumberPagination()
            paginated_sales = paginator.paginate_queryset(sales, request)

            # You can add serialization here
            sales_data = []
            for sale in sales:
                product_names = [
                    item.product_variant.product.name
                    for item in sale.items.all()
                ]

                sales_data.append({
                    "id": sale.id,
                    "client_name": sale.client.client_name if sale.client else "Walk-in Customer",
                    "total_amount": sale.total_amount,
                    "paid_amount": sale.paid_amount,
                    "discount": sale.discount,
                    "sale_date": sale.sale_date,
                    "created_at": sale.created_at,
                    "products": product_names
                })

            return paginator.get_paginated_response(sales_data)

        except Exception as e:
            logger.exception(f"Error retrieving sales for shop {shop_id}: {e}")
            return self.error_response("Unable to retrieve sales at this time.")

    # Allow viewing historical sales even if the shop's subscription has expired.
    @skip_subscription_validation
    @skip_feature_validation
    @extend_schema(
        tags=["Sales Management V3"],
        summary="Retrieve sale",
        description="Returns detailed information for a single sale including items and payment records.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Sale retrieved successfully."),
            404: OpenApiResponse(description="Exact message: The requested resource was not found"),
            403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Unable to retrieve sale details."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /api/v3/sales/sales/120/",
                value={"pk": 120},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Sale retrieved successfully.",
                    "data": {
                        "id": 120,
                        "client": {"id": 22, "name": "Rahul", "phone_1": 9876543210, "phone_2": None},
                        "staff_id": 3,
                        "staff_name": "Amit",
                        "address": "Andheri East",
                        "shop_sale_id": "SAL-120",
                        "description": "Festival sale",
                        "sale_date": "04-05-2026",
                        "total_amount": "2500.00",
                        "discount": "100.00",
                        "paid_amount": "1200.00",
                        "balance_due": "1200.00",
                        "created_at": "04-05-2026 11:42:12",
                        "items": [],
                        "payments": [],
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Not found response",
                value={
                    "status": "not_found",
                    "message": "The requested resource was not found",
                    "data": None,
                },
                response_only=True,
                status_codes=["404"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to retrieve sale details.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def retrieve(self, request, pk=None):
        """Retrieve a specific sale"""
        try:
            shop_id = self.shop_id
            sale = (
                Sale.objects.filter(shop_id=shop_id, id=pk)
                .select_related("client", "staff")
                .prefetch_related(
                    "items__product_variant__product__shop_service__general_service__main_category",
                    "items__product_variant",
                    "payments"
                )
                .first()
            )

            if not sale:
                return self.not_found_response("Sale not found.")

            created_at_local = localtime(sale.created_at).strftime("%d-%m-%Y %H:%M:%S")

            # Build detailed sale data
            sale_data = {
                "id": sale.id,
                "client": {
                    "id": sale.client.id if sale.client else None,
                    "name": sale.client.client_name if sale.client else "Walk-in Customer",
                    "phone_1": sale.client.client_phone_1 if sale.client else None,
                    "phone_2": sale.client.client_phone_2 if sale.client else None
                },
                "staff_id": sale.staff.id if sale.staff else None,
                "staff_name": sale.staff.name if sale.staff else None,
                "address": sale.address,
                "shop_sale_id": sale.shop_sale_id,
                "description": sale.description,
                "sale_date": sale.sale_date,
                "total_amount": sale.total_amount,
                "discount": sale.discount,
                "paid_amount": sale.paid_amount,
                "balance_due": sale.total_amount - sale.discount - sale.paid_amount,
                "created_at": created_at_local,
                "items": [],
                "payments": []
            }

            # Sale items
            for item in sale.items.all():
                variant = item.product_variant
                product = variant.product
                shop_service = product.shop_service
                general_service = shop_service.general_service
                main_category = general_service.main_category

                sale_data["items"].append({
                    "id": item.id,
                    "product_id": product.id,
                    "variant_id": variant.id,
                    "product_name": product.name,
                    "variant": variant.attribute,
                    "quantity": item.quantity,
                    "price": item.price,
                    "subtotal": item.subtotal,
                    "thumbnail": product.thumbnail_image,
                    "color": product.color,
                    "category": product.category,
                    "model": product.model,
                    "main_category_name": main_category.name if main_category else None
                })

            # Payment details
            for payment in sale.payments.all():
                sale_data["payments"].append({
                    "id": payment.id,
                    "amount": payment.payment_amount,
                    "method": payment.payment_method,
                    "date": payment.created_at
                })

            return self.success_response(
                data=sale_data,
                message="Sale retrieved successfully."
            )
        except Exception as e:
            return self.error_response("Unable to retrieve sale details.")
