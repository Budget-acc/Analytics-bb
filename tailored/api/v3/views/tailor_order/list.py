from tailored.models import TailorOrder, TailorOrderItem
from tailored.api.v3.serializers import TailorOrderListSerializer
from tailored.api.v3.utils.date_utils import parse_datetime
from ERP.utils.custom_pagination import StandardPageNumberPagination
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.db.models import Q, Prefetch, When, Case, Value, IntegerField, Exists, OuterRef
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation, skip_feature_validation
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="List tailor orders",
    description="Returns paginated tailor orders for the active shop with status, search, date, order-type, and assigned-tailor filters.",
    parameters=[
        OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Allowed values: all, completed, not_completed, initiated, in_progress."),
        OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search by shop_tailor_id, client name, staff name, or product name."),
        OpenApiParameter("date_from", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False, description="Created date lower bound in YYYY-MM-DD."),
        OpenApiParameter("date_to", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False, description="Created date upper bound in YYYY-MM-DD."),
        OpenApiParameter("customer_order_type", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Filter by order type. Allowed values: shop, rental, sale."),
        OpenApiParameter("assigned_tailor_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Tailor id filter."),
        OpenApiParameter("page", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Pagination page."),
    ],
    responses={
        200: OpenApiResponse(description="Paginated tailor order list response."),
        400: OpenApiResponse(description="Exact error messages:\n- Invalid status provided.\n- Invalid tailor ID provided.\n- Invalid 'date_from' format. Please use YYYY-MM-DD.\n- Invalid 'date_to' format. Please use YYYY-MM-DD.\n- Invalid customer_order_type provided. Allowed values: shop, rental, sale."),
        500: OpenApiResponse(description="Exact error message: Unable to retrieve tailor orders. Please try again later."),
    },
    examples=[OpenApiExample("List success", value={"count": 1, "next": None, "previous": None, "results": [{"id": 8, "shop_tailor_id": "TO-0008", "status": "initiated", "customer_order_type": "sale", "products": "Sherwani"}]}, response_only=True, status_codes=["200"])],
)
@skip_subscription_validation
@skip_feature_validation
def list(self, request):
    """
    List all Tailor Orders with optional filters and search.

    Status Filter Options:
    - No status param or status='all': Returns all orders
    - status='completed': Returns only completed orders
    - status='not_completed': Returns initiated and in_progress orders
    - status='initiated': Returns only initiated orders
    - status='in_progress': Returns only in_progress orders
    """
    try:
        # Base queryset with optimizations
        qs = TailorOrder.objects.select_related(
            "shop",
            "client",
            "created_by",
            "staff",
        ).prefetch_related(
            Prefetch(
                "items",
                queryset=TailorOrderItem.objects.select_related("assigned_tailor"),
            ),
            "additional_charges",
            "payments",
        ).filter(
            shop_id=self.shop_id,
            deleted__isnull=True  # Only non-deleted orders
        ).order_by("-created_at")

        # --- Apply Filters ---
        status = request.query_params.get("status")
        search = request.query_params.get("search")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        customer_order_type = request.query_params.get("customer_order_type")
        assigned_tailor_id = request.query_params.get("assigned_tailor_id")

        if status:
            status_lower = status.strip().lower()

            if status_lower == "completed":
                qs = qs.filter(status__in=["completed", "cancelled"])
            elif status_lower == "not_completed":
                qs = qs.exclude(status__in=["completed", "cancelled"])
            elif status_lower == "all":
                pass
            else:
                return self.validation_error_response(
                    message="Invalid status provided.",
                    dev_message=f"Invalid status filter: {status}. Valid options: all, completed, not_completed, initiated, in_progress."
                )

        if customer_order_type:
            order_type = customer_order_type.strip().lower()
            if order_type not in ["shop", "rental", "sale"]:
                return self.validation_error_response(
                    message="Invalid filter type provided.",
                    dev_message=f"Allowed values: shop, rental, sale, Provided: {customer_order_type}"
                )
            qs = qs.filter(customer_order_type=order_type)

        if assigned_tailor_id:
            try:
                tailor_id = int(assigned_tailor_id)
                qs = qs.filter(items__assigned_tailor_id=tailor_id).distinct()
            except (ValueError, TypeError):
                return self.validation_error_response(
                    message="Invalid tailor ID provided.",
                    dev_message=f"assigned_tailor_id is not a valid integer: {assigned_tailor_id}"
                )

        if date_from:
            try:
                from_date = parse_datetime(date_from).date() if date_from else None
                qs = qs.filter(pickup_date__date__gte=from_date)
            except (ValueError, TypeError):
                return self.validation_error_response(
                    message="Invalid 'date_from' format. Please use YYYY-MM-DD.",
                    dev_message=f"date_from parsing failed: {date_from}"
                )

        if date_to:
            try:
                to_date = parse_datetime(date_to).date() if date_to else None
                qs = qs.filter(pickup_date__date__lte=to_date)
            except (ValueError, TypeError):
                return self.validation_error_response(
                    message="Invalid 'date_to' format. Please use YYYY-MM-DD.",
                    dev_message=f"date_to parsing failed: {date_to}"
                )

        # --- Search ---
        if search:
            search_term = search.strip()

            product_match = TailorOrderItem.objects.filter(
                order_id=OuterRef("pk"),
                product_name__icontains=search_term
            )

            qs = qs.annotate(
                has_matching_product=Exists(product_match),

                search_rank=Case(
                    # Exact match
                    When(shop_tailor_id__iexact=search_term, then=Value(0)),
                    When(client__client_name__iexact=search_term, then=Value(0)),
                    When(staff__name__iexact=search_term, then=Value(0)),

                    # Starts with
                    When(shop_tailor_id__istartswith=search_term, then=Value(1)),
                    When(client__client_name__istartswith=search_term, then=Value(1)),
                    When(staff__name__istartswith=search_term, then=Value(1)),

                    # Product match
                    When(has_matching_product=True, then=Value(2)),

                    # Contains
                    default=Value(3),
                    output_field=IntegerField(),
                )
            ).filter(
                Q(shop_tailor_id__icontains=search_term) |
                Q(client__client_name__icontains=search_term) |
                Q(staff__name__icontains=search_term) |
                Q(has_matching_product=True)
            ).distinct().order_by("search_rank", "-created_at")

        # --- Pagination ---
        paginator = StandardPageNumberPagination()
        paginated_orders = paginator.paginate_queryset(qs, request)

        serializer = TailorOrderListSerializer(paginated_orders, many=True)

        return paginator.get_paginated_response(serializer.data)

    except Exception as e:
        logger.error(f"Error retrieving tailor orders for shop {self.shop_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to retrieve tailor orders. Please try again later.",
            dev_message=str(e)
        )
