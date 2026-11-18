import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status

from ERP.utils.custom_pagination import StandardPageNumberPagination
from ...helpers import MAX_ORDERED_COUNT, MAX_RANGE_DAYS, get_date_range, get_product_usage
from .schema_params import DATE_RANGE_ERRORS, END_DATE_PARAM, START_DATE_PARAM

logger = logging.getLogger("api_requests")


class UnusedProductsRetrieveMixin:

    @extend_schema(
        tags=["Trending Insights V3"],
        summary="Get unused and low-usage products",
        description=(
            "Returns the shop's unused products (0 orders) and low-usage products (1 to 5 orders) "
            "for the requested date range, each with its order count and last ordered date, "
            "ordered by order count ascending (0 orders first, then 1, 2, 3, 4, 5).\n\n"
            "Purpose: Surfaces dead and slow-moving stock so the shop owner/manager can review, "
            "promote, discount, or retire those products.\n\n"
            "Context: Called as a standalone drill-down, independent of the month-based Trending "
            "Insights sections, since stock review typically uses a custom date window. The same "
            "calculation is used by the unused-products/excel-report/ endpoint, so the Excel "
            "report always contains exactly these products, counts and dates.\n\n"
            "Order counting:\n"
            "- ordered_count = distinct Bookings (Booking created_at in the range) + distinct Sales "
            "(Sale sale_date in the range) containing the product. A booking/sale with several "
            "variants of the same product counts once.\n"
            "- Cancelled bookings (delivery_status = 'cancelled') and soft-deleted bookings never "
            "count, so a product whose only bookings were cancelled is returned with 0 orders. "
            "Deleted sales are removed permanently and never count.\n"
            "- Products with more than 5 orders are not returned.\n"
            "- last_ordered_date = the latest of the counted bookings' created_at date and sales' "
            "sale_date; null when ordered_count is 0.\n"
            "- Products with equal order counts are ordered newest product first.\n\n"
            "Inactive or soft-deleted products are never returned.\n\n"
            "The date range is capped at 180 days. A larger range is rejected immediately with a "
            "validation error, before the analytics query runs.\n\n"
            "Accepted inputs:\n"
            "Query parameters - Required:\n"
            "- start_date (YYYY-MM-DD or DD-MM-YYYY): start of the date range.\n"
            "- end_date (YYYY-MM-DD or DD-MM-YYYY): end of the date range (inclusive). Range must "
            "not exceed 180 days.\n\n"
            "Query parameters - Optional:\n"
            "- page (integer, >= 1): pagination page number.\n"
            "- page_size (integer): pagination page size.\n\n"
            "No path parameters or request body."
        ),
        parameters=[START_DATE_PARAM, END_DATE_PARAM],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Returns a paginated list of unused (0 orders) and low-usage (1-5 "
                    "orders) products for the requested date range.\n\n"
                    "Response shape:\n"
                    "- data.next / data.previous (string | null): pagination links.\n"
                    "- data.count (integer): total products with 0-5 orders in the range.\n"
                    "- data.page_size, data.current_page, data.total_pages, data.start_index, "
                    "data.end_index: standard pagination metadata.\n"
                    "- data.data (array): the products on this page, ordered by ordered_count "
                    "ascending. Each item: product_id, name, service_id (the product's "
                    "ShopOwnerService id), color, category, model, thumbnail_image, image, "
                    "main_service_name (MainServiceCategory.name via "
                    "shop_service.general_service.main_category, null if unset), ordered_count "
                    "(integer 0-5), last_ordered_date (string YYYY-MM-DD, or null when "
                    "ordered_count is 0)."
                )
            ),
            400: OpenApiResponse(
                description=f"Validation error. Exact messages: {DATE_RANGE_ERRORS}"
            ),
            403: OpenApiResponse(
                description=(
                    "Permission error. Exact message: Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(
                description="Exact message: Unable to retrieve unused products."
            ),
        },
        examples=[
            OpenApiExample(
                "Request example - YYYY-MM-DD",
                value={"start_date": "2026-04-01", "end_date": "2026-09-27"},
                request_only=True,
            ),
            OpenApiExample(
                "Request example - DD-MM-YYYY",
                value={"start_date": "01-04-2026", "end_date": "27-09-2026"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Unused products retrieved successfully.",
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
                                "product_id": 412, "name": "Old Turban", "service_id": 47,
                                "color": None, "category": "Turban", "model": "TB-01",
                                "thumbnail_image": "https://...", "image": "https://...",
                                "main_service_name": "Dresses",
                                "ordered_count": 0, "last_ordered_date": None,
                            },
                            {
                                "product_id": 309, "name": "Grey Suits", "service_id": 47,
                                "color": "Grey", "category": "Suits", "model": "Hiii",
                                "thumbnail_image": "https://...", "image": "https://...",
                                "main_service_name": "Dresses",
                                "ordered_count": 2, "last_ordered_date": "2026-07-15",
                            },
                        ],
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error response",
                value={"status": "error", "message": "Date range cannot exceed 180 days.", "data": None},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Permission denied response",
                value={"status": "permission_denied", "message": "Access to this shop has been deactivated.", "data": None},
                response_only=True,
                status_codes=["403"],
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        try:
            start_date = request.query_params.get("start_date")
            end_date = request.query_params.get("end_date")
            start, end = get_date_range(start_date, end_date, max_days=MAX_RANGE_DAYS)

            products = get_product_usage(self.shop_id, start, end, max_orders=MAX_ORDERED_COUNT)

            paginator = StandardPageNumberPagination()
            page = paginator.paginate_queryset(products, request)

            data = [
                {
                    "product_id": product.id,
                    "name": product.name,
                    "service_id": product.shop_service_id,
                    "color": product.color,
                    "category": product.category,
                    "model": product.model,
                    "thumbnail_image": product.thumbnail_image,
                    "image": product.image,
                    "main_service_name": (
                        product.shop_service.general_service.main_category.name
                        if product.shop_service.general_service.main_category
                        else None
                    ),
                    "ordered_count": product.ordered_count,
                    "last_ordered_date": (
                        product.last_ordered_date.isoformat() if product.last_ordered_date else None
                    ),
                }
                for product in page
            ]
            return paginator.get_paginated_response(data)

        except ValueError as e:
            return self.validation_error_response(
                errors={"detail": [str(e)]},
                message=str(e),
            )
        except Exception as e:
            logger.error(f"UnusedProducts error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve unused products.",
                dev_message=f"{type(e).__name__}: {e}",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
