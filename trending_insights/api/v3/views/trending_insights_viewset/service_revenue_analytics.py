import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from ...helpers import get_service_revenue_analytics, resolve_period
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


class ServiceRevenueAnalyticsRetrieveMixin:

    @extend_schema(
        tags=["Trending Insights V3"],
        summary="Get service-based revenue analytics",
        description=(
            "Returns per-Shop-Service revenue for the selected month or date range, with Booking and Sales "
            "counts/revenue kept separate, a product-level breakdown of the top 2 revenue-"
            "generating products plus a combined 'Others' bucket for the rest, and an overall "
            "summary across every service.\n\n"
            "Purpose: Lets the shop owner/manager see not just how much revenue each service "
            "generated, but which specific products are driving that revenue, and how much of it "
            "comes from Bookings vs Sales.\n\n"
            "Context: Called from the dashboard/insights screen for a chosen month or date range, as a "
            "standalone drill-down alongside the other Trending Insights sections. Uses the exact "
            "same completed-transaction eligibility and product->shop_service attribution rule as "
            "the Service Revenue section of the combined analytics/ endpoint (never "
            "`booking.service`, since that doesn't reflect the correct service for every "
            "individual product) - only completed, non-cancelled Bookings (by `completed_at`) and "
            "all Sales (by `sale_date`) in the requested window are counted, and it also works "
            "whether the shop only books, only sells, or does both.\n\n"
            "For each service, products are ranked by combined Booking+Sale revenue. The top 2 "
            "are returned individually; any further products are combined into a single 'Others' "
            "entry. A service with 2 or fewer distinct products has no 'Others' entry.\n\n"
            "Amounts: each product's `BookedProductVariant.amount` / `SaleItem.subtotal` gets its "
            "price-ratio share of the Booking/Sale discount deducted (for Bookings the discount "
            "is first shared between products and additional charges, as the stored booking tax "
            "does), then Exclusive tax is added at that Booking/Sale's stored tax rate; Inclusive "
            "tax is already inside the amount and is not added again. Each product line is "
            "rounded to 2 dp; amounts are returned as whole numbers when they have no paise.\n\n"
            "Counts: booking_count / sales_count are distinct Bookings / Sales per service - a "
            "Booking/Sale with several products in the same service counts once for it.\n\n"
            + PERIOD_INPUTS_DOC
            + "No path parameters or request body."
        ),
        parameters=[MONTH_PARAM, YEAR_PARAM, RANGE_START_DATE_PARAM, RANGE_END_DATE_PARAM],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Returns service revenue analytics for the selected period.\n\n"
                    "Response shape (all under `data`):\n"
                    + PERIOD_RESPONSE_DOC +
                    "- services (array): one entry per active Shop Service of the shop - services "
                    "with no activity in the period are included with all counts/revenues 0 and "
                    "product_breakdown [] - plus one 'Custom Work' entry when there are eligible "
                    "Tailor Orders, sorted by total_revenue descending (highest first; equal "
                    "totals by service_name). Each item:\n"
                    "  - service_id (integer, or null for Custom Work): the ShopOwnerService id.\n"
                    "  - service_name (string).\n"
                    "  - booking_count, sales_count (integer): distinct Bookings / Sales with at "
                    "least one product in the service.\n"
                    "  - total_transaction_count (integer): booking_count + sales_count.\n"
                    "  - booking_revenue, sales_revenue (number): Booking-only / Sale-only revenue "
                    "for the service (discount- and tax-adjusted, see above).\n"
                    "  - total_revenue (number): booking_revenue + sales_revenue.\n"
                    "  - product_breakdown (array): top 2 products by combined booking+sales "
                    "revenue, plus an 'Others' entry when more than 2 products contributed. Each "
                    "item: product_id (null for 'Others'), product_name, booking_amount, "
                    "sales_amount, total_amount (booking_amount + sales_amount), percentage "
                    "(integer, rounded, of the service's total_revenue). total_amount values sum "
                    "to exactly total_revenue.\n"
                    "  - Custom Work entry: completed Tailor Orders not converted into a booking "
                    "(created_booking null), by completed_at. service_id null, service_name "
                    "\"Custom Work\", booking/sales counts and revenues 0, total_transaction_count "
                    "= number of orders, total_revenue = sum of (customer_total_charge - "
                    "discount_amount) per order (never below 0 per order), product_breakdown [].\n"
                    "- overall_summary (object): totals across every service (Custom Work included "
                    "in total_transaction_count and total_revenue), independent of the "
                    "per-service breakdown. total_booking_count / total_sales_count are distinct "
                    "across all services (a Booking/Sale spanning several services counts once). "
                    "Keys: total_booking_count, total_sales_count, total_tailor_order_count "
                    "(Custom Work orders), total_transaction_count (booking + sales + tailor "
                    "order counts), total_booking_revenue, total_sales_revenue, "
                    "total_tailor_order_revenue (Custom Work revenue), total_revenue (booking + "
                    "sales + tailor order revenue)."
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
                description="Exact message: Unable to retrieve service revenue analytics."
            ),
        },
        examples=[
            OpenApiExample("Request example - month/year", value={"month": 9, "year": 2026}, request_only=True),
            OpenApiExample(
                "Request example - date range (DD-MM-YYYY)",
                value={"start_date": "01-07-2026", "end_date": "28-09-2026"},
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
                    "message": "Service revenue analytics retrieved successfully.",
                    "data": {
                        "month": 9,
                        "year": 2026,
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-30",
                        "services": [
                            {
                                "service_id": 1,
                                "service_name": "Bridal",
                                "booking_count": 96,
                                "sales_count": 24,
                                "total_transaction_count": 120,
                                "booking_revenue": 320000,
                                "sales_revenue": 80000,
                                "total_revenue": 400000,
                                "product_breakdown": [
                                    {
                                        "product_id": 101, "product_name": "Product A",
                                        "booking_amount": 160000, "sales_amount": 40000,
                                        "total_amount": 200000, "percentage": 50,
                                    },
                                    {
                                        "product_id": 102, "product_name": "Product B",
                                        "booking_amount": 80000, "sales_amount": 20000,
                                        "total_amount": 100000, "percentage": 25,
                                    },
                                    {
                                        "product_id": None, "product_name": "Others",
                                        "booking_amount": 80000, "sales_amount": 20000,
                                        "total_amount": 100000, "percentage": 25,
                                    },
                                ],
                            },
                            {
                                "service_id": None,
                                "service_name": "Custom Work",
                                "booking_count": 0,
                                "sales_count": 0,
                                "total_transaction_count": 2,
                                "booking_revenue": 0,
                                "sales_revenue": 0,
                                "total_revenue": 5340,
                                "product_breakdown": [],
                            },
                            {
                                "service_id": 7,
                                "service_name": "Jewellery",
                                "booking_count": 0,
                                "sales_count": 0,
                                "total_transaction_count": 0,
                                "booking_revenue": 0,
                                "sales_revenue": 0,
                                "total_revenue": 0,
                                "product_breakdown": [],
                            },
                        ],
                        "overall_summary": {
                            "total_booking_count": 96,
                            "total_sales_count": 24,
                            "total_tailor_order_count": 2,
                            "total_transaction_count": 122,
                            "total_booking_revenue": 320000,
                            "total_sales_revenue": 80000,
                            "total_tailor_order_revenue": 5340,
                            "total_revenue": 405340,
                        },
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

            result = get_service_revenue_analytics(self.shop_id, start, end)

            data = {
                **period,
                "services": result["services"],
                "overall_summary": result["overall_summary"],
            }
            return self.success_response(
                data=data, message="Service revenue analytics retrieved successfully."
            )

        except ValueError as e:
            return self.validation_error_response(
                errors={"detail": [str(e)]},
                message=str(e),
            )
        except Exception as e:
            logger.error(f"ServiceRevenueAnalytics error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve service revenue analytics.",
                dev_message=f"{type(e).__name__}: {e}",
            )
