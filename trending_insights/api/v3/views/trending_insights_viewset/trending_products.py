import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.exceptions import NotFound

from ERP.utils.custom_pagination import StandardPageNumberPagination

from ...helpers import get_trending_products, resolve_period
from .schema_params import (
    MONTH_PARAM,
    PAGE_PARAM,
    PERIOD_ERRORS,
    PERIOD_INPUTS_DOC,
    PERIOD_RESPONSE_DOC,
    RANGE_END_DATE_PARAM,
    RANGE_START_DATE_PARAM,
    YEAR_PARAM,
)

logger = logging.getLogger("api_requests")

TRENDING_PRODUCTS_PAGE_SIZE = 20


class TrendingProductsRetrieveMixin:

    @extend_schema(
        tags=["Trending Insights V3"],
        summary="Get paginated trending products",
        description=(
            "Returns the top 20 trending products for the selected month or date range, for the dashboard's "
            "'View More' flow beyond the top 5 shown by the analytics/ endpoint.\n\n"
            "Purpose: Lets the shop owner/manager browse further down the trending-products "
            "ranking than the dashboard's top-5 summary.\n\n"
            "Context: Called when the user taps 'View More' on the Trending Products section of "
            "the dashboard. Uses the exact same underlying ranking as analytics/ - the created-date "
            "Booking+Sale product-variant line-item count. Results are capped to the top 20 "
            "trending products for the period, so this endpoint always returns a single page.\n\n"
            + PERIOD_INPUTS_DOC
            + "Query parameters - Optional:\n"
            "- page (integer, >= 1): page number, default 1. 20 results max, all on a single page. "
            "A page beyond the last one returns 200 with an empty trending_products list rather "
            "than an error.\n\n"
            "No path parameters or request body."
        ),
        parameters=[MONTH_PARAM, YEAR_PARAM, RANGE_START_DATE_PARAM, RANGE_END_DATE_PARAM, PAGE_PARAM],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Returns the top 20 trending products for the selected period (always "
                    "a single page), using the project's standard pagination envelope.\n\n"
                    "Response shape:\n"
                    "- data.next, data.previous (string|null): links to the adjacent pages.\n"
                    "- data.count (integer): total trending products for the period.\n"
                    "- data.page_size (integer): results per page (20).\n"
                    "- data.current_page, data.total_pages (integer).\n"
                    "- data.start_index, data.end_index (integer): 1-based range of this page.\n"
                    "Period keys (under data.data):\n"
                    + PERIOD_RESPONSE_DOC +
                    "- data.data.trending_products (array): products for this page, ranked by "
                    "combined Booking+Sale line-item count (highest first); products with equal "
                    "counts are ordered by total_amount (highest first), then product_id. Each "
                    "item: product_id, name, "
                    "description, color, category, image, thumbnail_image, model (Product.model), "
                    "service_id (the product's ShopOwnerService id), main_service_name "
                    "(MainServiceCategory.name via shop_service.general_service.main_category, "
                    "null if unset), count, booking_total_amount (sum of "
                    "BookedProductVariant.amount), sales_total_amount (sum of SaleItem.subtotal), "
                    "total_amount (booking_total_amount + sales_total_amount; no discount is "
                    "applied to any of these), variants (array).\n"
                    "  - variants: every variant of the product. Variants with qualifying "
                    "Booking/Sale activity in the same window come first, ordered by count then "
                    "total_amount (both descending); variants with no activity follow with count "
                    "and amounts 0. Each item: variant_id, variant_name (ProductVariant.attribute, "
                    "e.g. a size; empty string if the variant has none), count (Booking+Sale "
                    "line items for that variant), booking_total_amount, sales_total_amount, "
                    "total_amount (booking_total_amount + sales_total_amount). Variant values "
                    "sum to the product's count and amounts.\n\n"
                    "Soft-deleted Bookings, cancelled Bookings (delivery_status = 'cancelled') and "
                    "deleted Sales are not counted - in product counts, amounts, ranking or variants."
                )
            ),
            400: OpenApiResponse(
                description=f"Validation error. Exact messages: {PERIOD_ERRORS}"
            ),
            403: OpenApiResponse(
                description=(
                    "Permission error. Exact message: Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(
                description="Exact message: Unable to retrieve trending products."
            ),
        },
        examples=[
            OpenApiExample("Request example - month/year", value={"month": 9, "year": 2026, "page": 2}, request_only=True),
            OpenApiExample(
                "Request example - date range (DD-MM-YYYY)",
                value={"start_date": "01-07-2026", "end_date": "28-09-2026", "page": 1},
                request_only=True,
            ),
            OpenApiExample(
                "Request example - date range (YYYY-MM-DD)",
                value={"start_date": "2026-07-01", "end_date": "2026-09-28"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Trending products retrieved successfully.",
                    "data": {
                        "next": None,
                        "previous": None,
                        "count": 14,
                        "page_size": 20,
                        "current_page": 1,
                        "total_pages": 1,
                        "start_index": 1,
                        "end_index": 14,
                        "data": {
                            "month": 9,
                            "year": 2026,
                            "start_date": "2026-09-01",
                            "end_date": "2026-09-30",
                            "trending_products": [
                                {
                                    "product_id": 205, "name": "Silk Saree", "image": "https://...",
                                    "model": "SLK-2026", "service_id": 42,
                                    "main_service_name": "Bridal Rentals",
                                    "count": 5, "booking_total_amount": 4000, "sales_total_amount": 1500,
                                    "total_amount": 5500,
                                    "variants": [
                                        {
                                            "variant_id": 881, "variant_name": "M", "count": 3,
                                            "booking_total_amount": 2500, "sales_total_amount": 500,
                                            "total_amount": 3000,
                                        },
                                        {
                                            "variant_id": 882, "variant_name": "L", "count": 2,
                                            "booking_total_amount": 1500, "sales_total_amount": 1000,
                                            "total_amount": 2500,
                                        },
                                        {
                                            "variant_id": 880, "variant_name": "S", "count": 0,
                                            "booking_total_amount": 0, "sales_total_amount": 0,
                                            "total_amount": 0,
                                        },
                                    ],
                                }
                            ],
                        },
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error response",
                value={"status": "error", "message": "month must be between 1 and 12.", "data": None},
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
            start, end, period = resolve_period(request.query_params)

            result = get_trending_products(
                self.shop_id, start, end, limit=TRENDING_PRODUCTS_PAGE_SIZE, include_variants=True
            )

            paginator = StandardPageNumberPagination()
            paginator.page_size = TRENDING_PRODUCTS_PAGE_SIZE
            try:
                paginated_products = paginator.paginate_queryset(result["results"], request)
            except NotFound:
                # Beyond the last page (results are capped to the top 20) - return an
                # empty page instead of erroring, since more pages simply don't exist.
                total_count = len(result["results"])
                page_size = TRENDING_PRODUCTS_PAGE_SIZE
                total_pages = -(-total_count // page_size) if total_count else 0
                try:
                    requested_page = int(request.query_params.get("page", "1"))
                except ValueError:
                    requested_page = total_pages + 1 if total_pages else 1
                data = {
                    "next": None,
                    "previous": None,
                    "count": total_count,
                    "page_size": page_size,
                    "current_page": requested_page,
                    "total_pages": total_pages,
                    "start_index": 0,
                    "end_index": 0,
                    "data": {
                        **period,
                        "trending_products": [],
                    },
                }
                return self.success_response(data=data, message="Trending products retrieved successfully.")

            data = {
                **period,
                "trending_products": paginated_products,
            }
            return paginator.get_paginated_response(data)

        except ValueError as e:
            return self.validation_error_response(
                errors={"detail": [str(e)]},
                message=str(e),
            )
        except Exception as e:
            logger.error(f"TrendingProducts error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve trending products.",
                dev_message=f"{type(e).__name__}: {e}",
            )
