import logging
from datetime import datetime

from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from ERP.utils.custom_pagination import StandardPageNumberPagination
from sales.models import Sale, SaleItem
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation, skip_feature_validation

logger = logging.getLogger('api_requests')


class SaleListMixin:

    # Allow viewing historical sales even if the shop's subscription has expired.
    @skip_subscription_validation
    @skip_feature_validation
    @extend_schema(
        tags=["Sales Management V5"],
        summary="List sales",
        description="Returns paginated shop sales with smart ranking on search and optional date-range filtering.",
        parameters=[
            OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Search by phone, sale id, or product name."),
            OpenApiParameter(name="from", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Start date filter. Format: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="to", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="End date filter. Format: YYYY-MM-DD or DD-MM-YYYY."),
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
            OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page size."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated sales list response."),
            403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Unable to retrieve sales at this time."),
        },
        examples=[
            OpenApiExample("Request example", value={"search": "98765", "from": "2026-05-01", "to": "2026-05-31", "page": 1}, request_only=True),
            OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 10, "client_phone": "9876543210", "client_phone_e164": "+919876543210", "total_amount": "2500.00", "paid_amount": "1000.00", "discount": "100.00", "sale_date": "04-05-2026", "created_at": "04-05-2026 11:20:10", "products": "Sherwani, Blazer", "staff_name": "Ravi", "staff_color": "#2F80ED", "shop_sale_id": "SAL-10"}]}, response_only=True, status_codes=["200"]),
            OpenApiExample("Server error response", value={"status": "error", "message": "Unable to retrieve sales at this time.", "data": None}, response_only=True, status_codes=["500"]),
        ],
    )
    def list(self, request):
        """List all sales for the shop"""
        try:
            shop_id = self.shop_id
            sales = (
                Sale.objects.filter(shop_id=shop_id)
                .select_related("staff", "client")
                .prefetch_related("items__product_variant__product")
            )

            search_query = request.query_params.get("search")

            is_search_applied = False

            if search_query:
                search_query = search_query.strip()

                if search_query:
                    is_search_applied = True

                    # ---- Product match using Exists (avoids duplicates) ----
                    matching_products = SaleItem.objects.filter(
                        sale_id=OuterRef("pk"),
                        product_variant__product__name__icontains=search_query
                    )

                    sales = sales.annotate(
                        has_matching_product=Exists(matching_products),

                        search_rank=Case(
                            # ===== EXACT MATCH =====
                            When(client_phone__iexact=search_query, then=Value(0)),
                            When(client__client_phone_1__iexact=search_query, then=Value(0)),
                            When(shop_sale_id__iexact=search_query, then=Value(0)),

                            # ===== STARTS WITH =====
                            When(client_phone__istartswith=search_query, then=Value(1)),
                            When(client__client_phone_1__istartswith=search_query, then=Value(1)),
                            When(shop_sale_id__istartswith=search_query, then=Value(1)),

                            # ===== PRODUCT NAME MATCH =====
                            When(has_matching_product=True, then=Value(2)),

                            # ===== CONTAINS =====
                            default=Value(3),
                            output_field=IntegerField(),
                        )
                    ).filter(
                        Q(client_phone__icontains=search_query) |
                        Q(client__client_phone_1__icontains=search_query) |
                        Q(shop_sale_id__icontains=search_query) |
                        Q(has_matching_product=True)
                    )

            if is_search_applied:
                sales = sales.order_by(
                    "search_rank",
                    "-created_at"
                )
            else:
                sales = sales.order_by("-created_at")

            # Date Range Filter
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

            sales_data = []
            for sale in paginated_sales:
                product_names = ", ".join([
                    item.product_variant.product.name
                    for item in sale.items.all()
                ])

                client_phone = (
                    sale.client_phone
                    or getattr(sale.client, "client_phone_1", None)
                )
                sales_data.append({
                    "id": sale.id,
                    "client_phone": client_phone,
                    "client_phone_e164": str(sale.client_phone_e164) if sale.client_phone_e164 else None,
                    "total_amount": sale.total_amount,
                    "paid_amount": sale.paid_amount,
                    "discount": sale.discount,
                    "sale_date": sale.sale_date,
                    "created_at": sale.created_at,
                    "products": product_names,
                    "staff_name": sale.staff.name if sale.staff else None,
                    "staff_color": sale.staff.color if sale.staff else None,
                    "shop_sale_id": sale.shop_sale_id
                })

            return paginator.get_paginated_response(sales_data)

        except Exception as e:
            logger.exception(f"Error retrieving sales for shop {shop_id}: {e}")
            return self.error_response("Unable to retrieve sales at this time.")
