import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from ...helpers import (
    get_service_revenue,
    get_staff_performance,
    get_trending_products,
    get_transaction_velocity,
    resolve_period,
)
from .schema_params import (
    MONTH_PARAM,
    PERIOD_ERRORS,
    PERIOD_INPUTS_DOC,
    PERIOD_RESPONSE_DOC,
    RANGE_END_DATE_PARAM,
    RANGE_START_DATE_PARAM,
    YEAR_PARAM,
)

logger = logging.getLogger("api_requests")

DASHBOARD_TRENDING_PRODUCTS_LIMIT = 5


class TrendingInsightsRetrieveMixin:

    @extend_schema(
        tags=["Trending Insights V3"],
        summary="Get trending insights",
        description=(
            "Returns a single combined analytics payload for the shop's owner/manager dashboard, "
            "covering four sections for the selected month or date range: Trending Products, Transaction "
            "Velocity, Service Revenue and Staff Performance.\n\n"
            "Purpose: Gives shop owners/managers a fast, one-request view of what's selling/renting "
            "well and how the business is performing during a given month, without them having to "
            "cross-reference separate Booking and Sale reports.\n\n"
            "Context: Called when the dashboard/insights screen loads for a chosen month or date range. Every "
            "section combines both Booking and Sale data, so it works correctly whether the shop "
            "only books, only sells, or does both.\n\n"
            "Two different date bases are used depending on the section:\n"
            "- Created-date sections (Trending Products, Transaction Velocity): based on when the "
            "Booking/Sale was created, regardless of pickup/return/completion date.\n"
            "- Completed-date sections (Service Revenue, Staff Performance): based on when the "
            "Booking was completed (Sales have no separate completion step and are always "
            "eligible). Cancelled bookings (delivery_status = 'cancelled') are excluded from "
            "Trending Products, Transaction Velocity and Service Revenue; Staff Performance "
            "follows the staff earnings report rules and does not exclude them.\n\n"
            "Trending Products counts each booked/sold product-variant line item individually "
            "(not deduplicated per booking/sale, and quantity is not factored in - one line item = "
            "one count), and returns only the top 5 here; use the separate trending-products/ "
            "endpoint to page through more.\n\n"
            "Transaction Velocity breakdown: for month/year requests, and for date ranges inside a "
            "single calendar month, transaction_velocity.weeks holds the weekly breakdown and "
            "transaction_velocity.months is empty. For date ranges spanning more than one calendar "
            "month (decided by comparing the calendar month/year of start_date and end_date, not "
            "by the number of days - e.g. 10 Aug to 2 Sep spans two), weeks is empty and months "
            "holds the per-month breakdown.\n\n"
            + PERIOD_INPUTS_DOC
            + "No path parameters or request body."
        ),
        parameters=[MONTH_PARAM, YEAR_PARAM, RANGE_START_DATE_PARAM, RANGE_END_DATE_PARAM],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Returns the combined trending insights for the selected period.\n\n"
                    "Response shape (all under `data`):\n"
                    + PERIOD_RESPONSE_DOC +
                    "- trending_products.results (array): top 5 products by combined Booking+Sale "
                    "line-item count (highest first); products with equal counts are ordered by "
                    "total_amount (highest first), then product_id - the same ranking as "
                    "trending-products/. Each item: product_id, name, description, color, category, "
                    "image, thumbnail_image, model (Product.model), service_id (the product's "
                    "ShopOwnerService id), main_service_name (MainServiceCategory.name via "
                    "shop_service.general_service.main_category, null if unset), count, "
                    "booking_total_amount (sum of BookedProductVariant.amount), sales_total_amount "
                    "(sum of SaleItem.subtotal), total_amount (booking_total_amount + "
                    "sales_total_amount; no discount is applied to any of these). The per-variant "
                    "breakdown is not included here - it is returned by trending-products/. "
                    "Soft-deleted "
                    "Bookings, cancelled Bookings and deleted Sales are not counted.\n"
                    "- trending_products.has_more (boolean): true if more than 5 products have "
                    "activity in the period (page through trending-products/ for the rest).\n"
                    "- transaction_velocity.weeks (array): one entry per calendar-day week bucket "
                    "(1-7, 8-14, 15-21, 22-28, 29-31). Each item: week, booking_count, sale_count, "
                    "total_count. For a date range inside one month only days within the range are "
                    "counted (buckets outside it are 0). Empty when a date range spans more than one "
                    "calendar month. Cancelled bookings are not counted.\n"
                    "- transaction_velocity.months (array): empty for month/year requests and for "
                    "single-month date ranges. For a date range spanning more than one calendar "
                    "month, one entry per calendar month the range touches, oldest first (months "
                    "with no activity included), each counting only days inside the range. Each "
                    "item: month (integer 1-12), year (integer), booking_count, sale_count, "
                    "total_count. Same counting rules as weeks.\n"
                    "- service_revenue (array): one entry per active Shop Service of the shop "
                    "(services with no activity in the period are included with all counts and "
                    "revenues 0). Each item: service_id, service_name, booking_count (distinct "
                    "Bookings with a product in the service), sales_count (distinct Sales with a "
                    "product in the service), total_transaction_count (booking_count + "
                    "sales_count), booking_revenue, sales_revenue, total_revenue "
                    "(booking_revenue + sales_revenue). Exactly the same calculation as the "
                    "service-revenue-analytics/ endpoint (without its product breakdown): only "
                    "completed, non-cancelled Bookings (by completed_at) and Sales (by "
                    "sale_date). Each product's BookedProductVariant.amount / SaleItem.subtotal "
                    "gets its price-ratio share of the Booking/Sale discount deducted, then "
                    "Exclusive tax added at that Booking/Sale's stored tax rate (Inclusive tax is "
                    "already inside the amount and is not added again), and is attributed to "
                    "that product's service. Amounts are whole numbers when they have no paise, "
                    "otherwise 2-dp numbers. Completed Custom Work (Tailor Orders not converted into a "
                    "booking, by completed_at) appears as one extra entry with service_id null "
                    "and service_name \"Custom Work\": booking/sales counts and revenues 0, "
                    "total_transaction_count = number of orders, total_revenue = sum of "
                    "(customer_total_charge - discount_amount) per order, never below 0 per order; "
                    "omitted when there are no such orders. Entries are ordered by total_revenue "
                    "(highest first; equal totals by service_name), the same order as "
                    "service-revenue-analytics/.\n"
                    "- staff_performance (array): one entry per staff member with activity in the "
                    "month, ordered by earned_amount (highest first). Same earnings rules as the "
                    "staff earnings report. Each item: staff_id, staff_name, booking_count "
                    "(completed bookings, by completed_at), sale_count (sales, by sale_date), "
                    "tailor_order_count (completed Custom Work orders not converted into a "
                    "booking, by completed_at), transaction_count (booking_count + sale_count + "
                    "tailor_order_count), earned_amount (bookings' paid advance_amount + sales' "
                    "total_amount - discount + tailor orders' paid_amount)."
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
                description="Exact message: Unable to retrieve trending insights."
            ),
        },
        examples=[
            OpenApiExample("Request example - month/year", value={"month": 9, "year": 2026}, request_only=True),
            OpenApiExample(
                "Request example - date range in one month (DD-MM-YYYY)",
                value={"start_date": "10-09-2026", "end_date": "25-09-2026"},
                request_only=True,
            ),
            OpenApiExample(
                "Request example - date range across months (YYYY-MM-DD)",
                value={"start_date": "2026-08-10", "end_date": "2026-09-02"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Trending insights retrieved successfully.",
                    "data": {
                        "month": 9,
                        "year": 2026,
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-30",
                        "trending_products": {
                            "results": [
                                {
                                    "product_id": 101, "name": "Bridal Lehenga", "image": "https://...",
                                    "model": "BL-2026", "service_id": 42,
                                    "main_service_name": "Bridal Rentals",
                                    "count": 18, "booking_total_amount": 194400, "sales_total_amount": 0,
                                    "total_amount": 194400,
                                }
                            ],
                            "has_more": True,
                        },
                        "transaction_velocity": {
                            "weeks": [{"week": 1, "booking_count": 28, "sale_count": 12, "total_count": 40}],
                            "months": [],
                        },
                        "service_revenue": [
                            {
                                "service_id": 1, "service_name": "Bridal Rentals",
                                "booking_count": 81, "sales_count": 15, "total_transaction_count": 96,
                                "booking_revenue": 194400, "sales_revenue": 32000, "total_revenue": 226400,
                            },
                            {
                                "service_id": None, "service_name": "Custom Work",
                                "booking_count": 0, "sales_count": 0, "total_transaction_count": 2,
                                "booking_revenue": 0, "sales_revenue": 0, "total_revenue": 5340,
                            },
                        ],
                        "staff_performance": [
                            {
                                "staff_id": 10, "staff_name": "Staff A", "booking_count": 12,
                                "sale_count": 8, "tailor_order_count": 2, "transaction_count": 22,
                                "earned_amount": 53000,
                            }
                        ],
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Success response - date range across months (10-08-2026 to 02-09-2026)",
                value={
                    "status": "success",
                    "message": "Trending insights retrieved successfully.",
                    "data": {
                        "month": 8,
                        "year": 2026,
                        "start_date": "2026-08-10",
                        "end_date": "2026-09-02",
                        "trending_products": {"results": ["..."], "has_more": True},
                        "transaction_velocity": {
                            "weeks": [],
                            "months": [
                                {"month": 8, "year": 2026, "booking_count": 64, "sale_count": 21, "total_count": 85},
                                {"month": 9, "year": 2026, "booking_count": 7, "sale_count": 2, "total_count": 9},
                            ],
                        },
                        "service_revenue": ["..."],
                        "staff_performance": ["..."],
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error response",
                value={"status": "error", "message": "month and year are required.", "data": None},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Validation error response - range too long",
                value={"status": "error", "message": "Date range cannot exceed 90 days.", "data": None},
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

            trending = get_trending_products(
                self.shop_id, start, end, limit=DASHBOARD_TRENDING_PRODUCTS_LIMIT,
                include_variants=False,
            )
            velocity = get_transaction_velocity(self.shop_id, start, end)
            service_revenue = get_service_revenue(self.shop_id, start, end)
            staff_performance = get_staff_performance(self.shop_id, start, end)

            data = {
                **period,
                "trending_products": trending,
                "transaction_velocity": velocity,
                "service_revenue": service_revenue,
                "staff_performance": staff_performance,
            }
            return self.success_response(data=data, message="Trending insights retrieved successfully.")

        except ValueError as e:
            return self.validation_error_response(
                errors={"detail": [str(e)]},
                message=str(e),
            )
        except Exception as e:
            logger.error(f"TrendingInsights error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve trending insights.",
                dev_message=f"{type(e).__name__}: {e}",
            )
