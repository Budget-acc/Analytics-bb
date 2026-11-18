import logging
from collections import defaultdict
from rest_framework.decorators import action
from django.db.models import F
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from services.models import ProductVariant
from tailored.models import TailorOrderItem
from ERP.utils.custom_pagination import StandardPageNumberPagination

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="List material usage orders by variant",
    description="Returns paginated tailor orders that consumed a specific material variant.",
    parameters=[
        OpenApiParameter("variant_id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Material variant id."),
        OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Order status filter. Default all."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated order usage response."),
        400: OpenApiResponse(
            description=(
                "Validation messages:\n"
                "- Invalid product variant ID provided.\n"
                "- Invalid status parameter."
            )
        ),
        404: OpenApiResponse(description="Exact message: Product variant not found."),
        500: OpenApiResponse(description="Exact message: Unable to fetch order information. Please try again."),
    },
    examples=[
        OpenApiExample("Success response", value={"count": 1, "next": None, "previous": None, "results": [{"id": 98, "tailor_name": "Raju", "status": "completed", "products": "Kurta"}]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Invalid status", value={"status": "validation_error", "message": "Invalid status parameter.", "errors": {}}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=['get'], url_path=r'material-usage-orders/(?P<variant_id>\d+)')
def material_usage_orders(self, request, variant_id=None):
    # Validate variant_id
    try:
        variant_id = int(variant_id)
    except (ValueError, TypeError):
        return self.validation_error_response(
            message="Invalid product variant ID provided.",
            dev_message=f"variant_id is not a valid integer: {variant_id}"
        )

    # Status filter
    status_filter = request.query_params.get('status', 'all').lower().strip()
    valid_statuses = ['initiated', 'in_progress', 'completed', 'cancelled', 'all']

    if status_filter not in valid_statuses:
        return self.validation_error_response(
            message="Invalid status parameter.",
            dev_message=f"Invalid status filter: {status_filter}. Valid options: {valid_statuses}"
        )

    try:
        # Validate variant belongs to current shop
        variant_exists = ProductVariant.objects.filter(
            id=variant_id,
            product__shop_service__shop_id=self.shop_id,
            deleted__isnull=True,
            is_active=True
        ).exists()
        if not variant_exists:
            return self.not_found_response(
                resource="Product Variant",
                message="Product variant not found.",
                dev_message=f"Variant {variant_id} not found in shop {self.shop_id}"
            )

        # Base queryset (optimized)
        order_items_qs = TailorOrderItem.objects.select_related(
            'order',
            'assigned_tailor'
        ).filter(
            material_usages__material_id=variant_id,
            order__shop_id=self.shop_id,
            order__deleted__isnull=True,
            deleted__isnull=True
        )

        # Apply status filter if not "all"
        if status_filter != 'all':
            order_items_qs = order_items_qs.filter(order__status=status_filter)

        # Annotate necessary fields
        order_items_qs = order_items_qs.annotate(
            tailor_order_id=F('order__id'),
            tailor_name=F('assigned_tailor__name'),
            pickup_date=F('expected_completion_date'),  # use expected_completion_date
            order_status=F('order__status'),
            item_product_name=F('product_name')
        ).values(
            'tailor_order_id',
            'tailor_name',
            'pickup_date',
            'order_status',
            'item_product_name'
        ).distinct().order_by('-order__completed_at', '-order__created_at')

        # Pagination
        paginator = StandardPageNumberPagination()
        paginated_items = paginator.paginate_queryset(order_items_qs, request)

        # Group data
        orders_dict = defaultdict(lambda: {
            'tailor_name': None,
            'pickup_date': None,
            'status': None,
            'products': []
        })

        for item in paginated_items:
            order_id = item['tailor_order_id']
            orders_dict[order_id]['tailor_name'] = item['tailor_name']
            orders_dict[order_id]['pickup_date'] = item['pickup_date']
            orders_dict[order_id]['status'] = item['order_status']
            if item['item_product_name'] and item['item_product_name'] not in orders_dict[order_id]['products']:
                orders_dict[order_id]['products'].append(item['item_product_name'])

        # Format response
        response_data = []
        for order_id, details in orders_dict.items():
            pickup_date_formatted = None
            if details['pickup_date']:
                local_dt = localtime(details['pickup_date'])
                pickup_date_formatted = local_dt.strftime("%d-%m-%Y %H:%M:%S")

            response_data.append({
                "id": order_id,
                "tailor_name": details['tailor_name'] or "No Tailor Assigned",
                "pickup_date": pickup_date_formatted,
                "status": details['status'],
                "products": ", ".join(details['products']) if details['products'] else "No Products"
            })

        return paginator.get_paginated_response(response_data)

    except Exception as e:
        logger.error(f"Error fetching material usage orders for variant {variant_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to fetch order information. Please try again.",
            dev_message=f"Material usage orders fetch failed: {str(e)}"
        )
