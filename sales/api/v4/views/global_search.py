import logging
from collections import defaultdict
from itertools import chain

from django.db.models import Case, CharField, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Lower
from django.utils.timezone import localtime
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from booking_v2.models import BookingV2, BookedProductVariant
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.mixins import StandardResponseMixin
from sales.models import Sale, SaleItem
from shop.api.v3.utils.shop import get_active_shop_id
from tailored.models import TailorOrder, TailorOrderItem
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from ..utils.date_utils import parse_custom_date

logger = logging.getLogger('api_requests')


def _build_search_queryset(qs, product_items_qs, product_name_field, id_field, client_name_field, client_phone_field,
                           staff_name_field, search_query, date_filter, record_type, entity_offset):
    """
    Shared helper: annotates, filters, and labels a queryset for global search.
    Avoids duplicating the annotate→filter→record_type pattern across three model types.

    Ranking (relative to entity_offset), grouping results by entity first, then by
    field priority (Invoice ID > Client Name > Staff Name > Product Name), then by
    match type (exact > starts with > contains):
      +0/1/2   — invoice id exact / startswith / contains
      +3/4/5   — client name exact / startswith / contains
      +6/7/8   — staff name exact / startswith / contains
      +9/10/11 — product name exact / startswith / contains
      +12/13/14 — client phone (E.164) exact / startswith / contains

    sort_key carries the matched value (lowercased) for dictionary-order tie-breaking
    within the same rank bucket.
    """
    product_exact_qs = product_items_qs.filter(**{f"{product_name_field}__iexact": search_query})
    product_startswith_qs = product_items_qs.filter(**{f"{product_name_field}__istartswith": search_query})
    product_contains_qs = product_items_qs.filter(**{f"{product_name_field}__icontains": search_query})
    matching_product_name_sq = Subquery(
        product_contains_qs.order_by(product_name_field).values(product_name_field)[:1]
    )

    return (
        qs
        .annotate(
            has_product_exact=Exists(product_exact_qs),
            has_product_startswith=Exists(product_startswith_qs),
            has_product_contains=Exists(product_contains_qs),
            search_rank=Case(
                # Tier 1: Invoice ID
                When(**{f"{id_field}__iexact": search_query},      then=Value(entity_offset + 0)),
                When(**{f"{id_field}__istartswith": search_query}, then=Value(entity_offset + 1)),
                When(**{f"{id_field}__icontains": search_query},   then=Value(entity_offset + 2)),
                # Tier 2: Client name
                When(**{f"{client_name_field}__iexact": search_query},      then=Value(entity_offset + 3)),
                When(**{f"{client_name_field}__istartswith": search_query}, then=Value(entity_offset + 4)),
                When(**{f"{client_name_field}__icontains": search_query},   then=Value(entity_offset + 5)),
                # Tier 3: Staff name
                When(**{f"{staff_name_field}__iexact": search_query},      then=Value(entity_offset + 6)),
                When(**{f"{staff_name_field}__istartswith": search_query}, then=Value(entity_offset + 7)),
                When(**{f"{staff_name_field}__icontains": search_query},   then=Value(entity_offset + 8)),
                # Tier 4: Product name (lowest priority)
                When(has_product_exact=True,      then=Value(entity_offset + 9)),
                When(has_product_startswith=True, then=Value(entity_offset + 10)),
                When(has_product_contains=True,   then=Value(entity_offset + 11)),
                # Tier 5: Client phone number
                When(**{f"{client_phone_field}__iexact": search_query},      then=Value(entity_offset + 12)),
                When(**{f"{client_phone_field}__istartswith": search_query}, then=Value(entity_offset + 13)),
                When(**{f"{client_phone_field}__icontains": search_query},   then=Value(entity_offset + 14)),
                default=Value(entity_offset + 15),
                output_field=IntegerField(),
            ),
            sort_key=Case(
                When(**{f"{id_field}__icontains": search_query}, then=Lower(F(id_field))),
                When(**{f"{client_name_field}__icontains": search_query}, then=Lower(F(client_name_field))),
                When(**{f"{staff_name_field}__icontains": search_query}, then=Lower(F(staff_name_field))),
                When(**{f"{client_phone_field}__icontains": search_query}, then=Lower(F(client_phone_field))),
                default=Lower(matching_product_name_sq),
                output_field=CharField(),
            ),
        )
        .filter(
            (
                Q(**{f"{client_name_field}__icontains": search_query})
                | Q(**{f"{staff_name_field}__icontains": search_query})
                | Q(**{f"{id_field}__icontains": search_query})
                | Q(has_product_contains=True)
                | Q(**{f"{client_phone_field}__icontains": search_query})
            ) & date_filter
        )
        .annotate(record_type=Value(record_type, output_field=CharField()))
    )


class GlobalSearchViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Global search endpoint to search across bookings, sales, and tailor orders.

    Searches in:
    - Client names (booking_v2.Client)
    - Client phone numbers (client phone 1 / sale client phone)
    - Staff names (shop.Staff)
    - Shop booking IDs
    - Shop sale IDs
    - Shop tailor IDs
    """
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]
    authentication_classes = [CutoffJWTAuthentication]
    pagination_class = StandardPageNumberPagination

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in GlobalSearchViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })

    @extend_schema(
        tags=["Sales Management V4"],
        summary="Global search",
        description="Searches across bookings, sales, and tailor orders by term and optional date range.",
        parameters=[
            OpenApiParameter(name="search", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=True, description="Search term matched against ids, client names, client phone numbers, staff names, and product names."),
            OpenApiParameter(name="start_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Optional start date filter."),
            OpenApiParameter(name="end_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Optional end date filter."),
            OpenApiParameter(name="page", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page number."),
            OpenApiParameter(name="page_size", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="Pagination page size."),
        ],
        responses={
            200: OpenApiResponse(description="Paginated unified search results."),
            403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
            500: OpenApiResponse(description="Exact message: Something went wrong while searching. Please try again."),
        },
        examples=[
            OpenApiExample("Request example", value={"search": "SAL-10", "start_date": "2026-05-01", "end_date": "2026-05-31", "page": 1}, request_only=True),
            OpenApiExample("Success response", value={"count": 2, "next": None, "previous": None, "results": [{"id": 10, "client_phone": "9876543210", "total_amount": 2500, "paid_amount": 1000, "discount": 100, "sale_date": "2026-05-04", "created_at": "04-05-2026 11:20:10", "products": "Sherwani, Blazer", "staff_color": "#2F80ED", "type": "sale"}]}, response_only=True, status_codes=["200"]),
            OpenApiExample("Server error response", value={"status": "error", "message": "Something went wrong while searching. Please try again.", "data": None}, response_only=True, status_codes=["500"]),
        ],
    )
    @action(detail=False, methods=['get'], url_path='search')
    def search(self, request):
        """
        Global search across bookings, sales, and tailor orders.

        Query Parameters:
        - search: Search term (required)
        - start_date: Start date for filtering (optional)
        - end_date: End date for filtering (optional)

        Searches in:
        - Client name
        - Client phone number
        - Staff name
        - Invoice IDs (shop_booking_id, shop_sale_id, shop_tailor_id)

        Returns paginated results sorted by created_at (most recent first)
        """
        try:
            # Get search query
            search_query = request.query_params.get("search", "").strip()

            # Get date filters
            start_date_str = request.query_params.get("start_date")
            end_date_str = request.query_params.get("end_date")

            start_date = parse_custom_date(start_date_str) if start_date_str else None
            end_date = parse_custom_date(end_date_str) if end_date_str else None

            # Build base date filter
            date_filter = Q()
            if start_date and end_date:
                date_filter = Q(created_at__date__range=(start_date, end_date))
            elif start_date:
                date_filter = Q(created_at__date__gte=start_date)
            elif end_date:
                date_filter = Q(created_at__date__lte=end_date)

            # Search Bookings
            # .only() avoids loading large unused JSONFields (documents, details, documents_details, etc.)
            bookings = _build_search_queryset(
                qs=BookingV2.objects.filter(
                    shop_id=self.shop_id,
                    deleted__isnull=True
                ).select_related("client_identity", "staff").only(
                    "id", "shop_booking_id", "booking_status", "delivery_status",
                    "created_at", "pickup_date", "return_date", "cooling_period_end",
                    "client_identity_id", "staff_id",
                ),
                product_items_qs=BookedProductVariant.objects.filter(
                    booking_id=OuterRef("pk"),
                ),
                product_name_field="product_name",
                id_field="shop_booking_id",
                client_name_field="client_identity__client_name",
                client_phone_field="client_identity__phone_1_e164",
                staff_name_field="staff__name",
                search_query=search_query,
                date_filter=date_filter,
                record_type="booking",
                entity_offset=0,
            )

            # Search Sales
            sales = _build_search_queryset(
                qs=Sale.objects.filter(
                    shop_id=self.shop_id
                ).select_related("client", "staff").only(
                    "id", "shop_sale_id", "total_amount", "paid_amount", "discount",
                    "sale_date", "created_at", "client_phone",
                    "client_id", "staff_id",
                ),
                product_items_qs=SaleItem.objects.filter(
                    sale_id=OuterRef("pk"),
                ),
                product_name_field="product_variant__product__name",
                id_field="shop_sale_id",
                client_name_field="client__client_name",
                client_phone_field="client_phone_e164",
                staff_name_field="staff__name",
                search_query=search_query,
                date_filter=date_filter,
                record_type="sale",
                entity_offset=100,
            )

            # Search Tailor Orders
            tailor_orders = _build_search_queryset(
                qs=TailorOrder.objects.filter(
                    shop_id=self.shop_id,
                    deleted__isnull=True
                ).select_related("client", "staff").only(
                    "id", "shop_tailor_id", "status", "customer_order_type",
                    "pickup_date", "created_at",
                    "client_id", "staff_id",
                ),
                product_items_qs=TailorOrderItem.objects.filter(
                    order_id=OuterRef("pk"),
                ),
                product_name_field="product_name",
                id_field="shop_tailor_id",
                client_name_field="client__client_name",
                client_phone_field="client__phone_1_e164",
                staff_name_field="staff__name",
                search_query=search_query,
                date_filter=date_filter,
                record_type="tailor_order",
                entity_offset=200,
            )

            # Combine all results and sort by search_rank, then sort_key, then
            # recency as a final deterministic tiebreaker - without it, rows
            # tied on (search_rank, sort_key) have no defined order (none of
            # the underlying querysets specify .order_by()), which can shift
            # which records land on which page between requests.
            # NOTE: This Python-side sort loads all matched records into memory.
            # For future scalability consider a UNION query approach.
            combined_results = sorted(
                chain(bookings, sales, tailor_orders),
                key=lambda x: (
                    getattr(x, "search_rank", 9999),
                    getattr(x, "sort_key", "") or "",
                    -x.created_at.timestamp() if x.created_at else 0,
                ),
            )

            # Paginate on model objects — only the current page slice is transformed.
            paginator = StandardPageNumberPagination()
            page = paginator.paginate_queryset(combined_results, request)

            # Post-pagination targeted fetches: load child items only for the records
            # on the current page (avoids prefetch_related on the full result set).
            booking_ids = [r.id for r in page if r.record_type == 'booking']
            sale_ids    = [r.id for r in page if r.record_type == 'sale']
            tailor_ids  = [r.id for r in page if r.record_type == 'tailor_order']

            booked_items_map = defaultdict(list)
            if booking_ids:
                for item in BookedProductVariant.objects.filter(booking_id__in=booking_ids):
                    booked_items_map[item.booking_id].append(item)

            sale_items_map = defaultdict(list)
            if sale_ids:
                for item in SaleItem.objects.select_related("product_variant__product").filter(sale_id__in=sale_ids):
                    sale_items_map[item.sale_id].append(item)

            tailor_items_map = defaultdict(list)
            if tailor_ids:
                for item in TailorOrderItem.objects.filter(order_id__in=tailor_ids):
                    tailor_items_map[item.order_id].append(item)

            # Transform only the current page
            transformed_page = []
            for record in page:
                if record.record_type == 'booking':
                    items = booked_items_map.get(record.id, [])
                    products = ", ".join([
                        item.product_name
                        for item in items
                    ])

                    transformed_page.append({
                        "id": record.id,
                        "client": record.client_identity.client_name if record.client_identity else None,
                        "pickup_date": localtime(record.pickup_date).strftime("%d-%m-%Y %H:%M:%S") if record.pickup_date else None,
                        "return_date": localtime(record.return_date).strftime("%d-%m-%Y %H:%M:%S") if record.return_date else None,
                        "cooling_period_end": localtime(record.cooling_period_end).strftime("%d-%m-%Y %H:%M:%S") if record.cooling_period_end else None,
                        "booking_status": record.booking_status,
                        "delivery_status": record.delivery_status,
                        "created_at": localtime(record.created_at).strftime("%d-%m-%Y %H:%M:%S"),
                        "products": products,
                        "staff_color": record.staff.color if record.staff else None,
                        "type": "booking"
                    })

                elif record.record_type == 'sale':
                    items = sale_items_map.get(record.id, [])
                    products = ", ".join([
                        item.product_variant.product.name
                        for item in items
                    ])

                    client_phone = (
                        record.client_phone
                        or (record.client.client_phone_1 if record.client else None)
                    )

                    transformed_page.append({
                        "id": record.id,
                        "client_phone": client_phone,
                        "total_amount": int(record.total_amount),
                        "paid_amount": int(record.paid_amount),
                        "discount": int(record.discount),
                        "sale_date": record.sale_date.strftime("%Y-%m-%d"),
                        "created_at": localtime(record.created_at).strftime("%d-%m-%Y %H:%M:%S"),
                        "products": products,
                        "staff_color": record.staff.color if record.staff else None,
                        "type": "sale"
                    })

                elif record.record_type == 'tailor_order':
                    items = tailor_items_map.get(record.id, [])
                    products = ", ".join([
                        item.product_name
                        for item in items
                    ])

                    transformed_page.append({
                        "id": record.id,
                        "status": record.status,
                        "customer_order_type": record.customer_order_type,
                        "pickup_date": localtime(record.pickup_date).strftime("%d-%m-%Y %H:%M:%S") if record.pickup_date else None,
                        "created_at": localtime(record.created_at).strftime("%d-%m-%Y %H:%M:%S"),
                        "products": products,
                        "staff_color": record.staff.color if record.staff else None,
                        "type": "tailor_order"
                    })

            return paginator.get_paginated_response(transformed_page)

        except Exception as e:
            logger.error(f"Global search error for shop {self.shop_id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while searching. Please try again.",
                dev_message=f"GlobalSearchViewSet error: {str(e)}"
            )
