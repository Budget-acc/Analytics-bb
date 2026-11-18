import logging

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from ...helpers import get_top_trending_color_category, resolve_period
from .schema_params import (
    MONTH_PARAM,
    PERIOD_ERRORS,
    PERIOD_INPUTS_DOC,
    PERIOD_RESPONSE_DOC,
    RANGE_END_DATE_PARAM,
    RANGE_START_DATE_PARAM,
    TYPE_PARAM,
    YEAR_PARAM,
)

logger = logging.getLogger("api_requests")

VALID_TYPES = {"color", "category"}


class TrendingColorCategoryRetrieveMixin:

    @extend_schema(
        tags=["Trending Insights V3"],
        summary="Get top trending colors and categories",
        description=(
            "Returns the top 5 most-booked/sold product Colors and the top 5 most-booked/sold "
            "product Categories for the selected month or date range (fewer if fewer exist), each with a "
            "breakdown of the other attribute's contribution within it.\n\n"
            "Purpose: Lets the shop owner/manager see not just which colors/categories are "
            "trending, but what's driving that demand - e.g. which categories make up each top "
            "color's bookings, or which colors make up each top category's bookings.\n\n"
            "Context: Called from the dashboard/insights screen for a chosen month or date range, alongside "
            "the other Trending Insights sections. Combines both Booking and Sale data (same "
            "created-date, line-item counting rules as the Trending Products section), so it "
            "works correctly whether the shop only books, only sells, or does both. Each booked/"
            "sold product-variant line item counts individually (not deduplicated per booking/"
            "sale, and quantity is not factored in - one line item = one count). Cancelled bookings "
            "(delivery_status = 'cancelled') and soft-deleted bookings are excluded, so their "
            "products never add to any color/category count or breakdown.\n\n"
            "Color and category values are grouped case-insensitively with leading/trailing "
            "spaces ignored, and returned in Title Case - e.g. 'red', 'RED' and 'Red ' are "
            "combined into one 'Red' entry, in the top lists and in both breakdowns.\n\n"
            "Products with a blank/null color or category are grouped under 'Uncategorized' so "
            "totals always reconcile, but 'Uncategorized' can never itself be one of the top "
            "colors or top categories (it can still appear inside a breakdown).\n\n"
            "By default both top_trending_color and top_trending_category are returned; use the "
            "optional type parameter to request only one.\n\n"
            + PERIOD_INPUTS_DOC
            + "Query parameters - Optional:\n"
            "- type (string, 'color' or 'category'): restricts the response to one section.\n\n"
            "No path parameters or request body."
        ),
        parameters=[MONTH_PARAM, YEAR_PARAM, RANGE_START_DATE_PARAM, RANGE_END_DATE_PARAM, TYPE_PARAM],
        responses={
            200: OpenApiResponse(
                description=(
                    "Success. Returns the top trending colors and/or categories for the selected period.\n\n"
                    "Response shape (all under `data`):\n"
                    + PERIOD_RESPONSE_DOC +
                    "- top_trending_color (array, omitted if type=category): up to 5 colors ordered "
                    "by booking_count descending; fewer if fewer exist, empty if no product with a "
                    "non-blank color had activity in the period. Each item: color (string), "
                    "booking_count (integer, combined Booking+Sale count), category_breakdown "
                    "(array of {category, booking_count}, sums to booking_count).\n"
                    "- top_trending_category (array, omitted if type=color): up to 5 categories "
                    "ordered by booking_count descending; fewer if fewer exist, empty if no product "
                    "with a non-blank category had activity in the period. Each item: category "
                    "(string), booking_count (integer), color_breakdown (array of {color, "
                    "booking_count}, sums to booking_count)."
                )
            ),
            400: OpenApiResponse(
                description=(
                    f"Validation error. Exact messages: {PERIOD_ERRORS}; "
                    "type must be 'color' or 'category'."
                )
            ),
            403: OpenApiResponse(
                description=(
                    "Permission error. Exact message: Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(
                description="Exact message: Unable to retrieve top trending color/category."
            ),
        },
        examples=[
            OpenApiExample("Request example - both sections", value={"month": 9, "year": 2026}, request_only=True),
            OpenApiExample("Request example - color only", value={"month": 9, "year": 2026, "type": "color"}, request_only=True),
            OpenApiExample(
                "Request example - date range (DD-MM-YYYY)",
                value={"start_date": "01-09-2026", "end_date": "30-09-2026"},
                request_only=True,
            ),
            OpenApiExample(
                "Request example - date range (YYYY-MM-DD), category only",
                value={"start_date": "2026-08-10", "end_date": "2026-09-02", "type": "category"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Top trending color/category retrieved successfully.",
                    "data": {
                        "month": 9,
                        "year": 2026,
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-30",
                        "top_trending_color": [
                            {
                                "color": "Black",
                                "booking_count": 100,
                                "category_breakdown": [
                                    {"category": "Jodhpuri", "booking_count": 50},
                                    {"category": "Sherwani", "booking_count": 30},
                                    {"category": "Suit", "booking_count": 20},
                                ],
                            },
                            {
                                "color": "Blue",
                                "booking_count": 80,
                                "category_breakdown": [
                                    {"category": "Suit", "booking_count": 45},
                                    {"category": "Sherwani", "booking_count": 25},
                                    {"category": "Uncategorized", "booking_count": 10},
                                ],
                            },
                        ],
                        "top_trending_category": [
                            {
                                "category": "Jodhpuri",
                                "booking_count": 100,
                                "color_breakdown": [
                                    {"color": "Black", "booking_count": 40},
                                    {"color": "White", "booking_count": 30},
                                    {"color": "Yellow", "booking_count": 20},
                                    {"color": "Green", "booking_count": 10},
                                ],
                            },
                            {
                                "category": "Sherwani",
                                "booking_count": 75,
                                "color_breakdown": [
                                    {"color": "Black", "booking_count": 40},
                                    {"color": "Blue", "booking_count": 35},
                                ],
                            },
                        ],
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error response",
                value={"status": "error", "message": "type must be 'color' or 'category'.", "data": None},
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

            type_param = (request.query_params.get("type") or "").strip().lower()
            if type_param and type_param not in VALID_TYPES:
                raise ValueError("type must be 'color' or 'category'.")
            section = type_param or None

            sections = get_top_trending_color_category(self.shop_id, start, end, section=section)

            data = {
                **period,
                **sections,
            }
            return self.success_response(
                data=data, message="Top trending color/category retrieved successfully."
            )

        except ValueError as e:
            return self.validation_error_response(
                errors={"detail": [str(e)]},
                message=str(e),
            )
        except Exception as e:
            logger.error(f"TrendingColorCategory error: {e}", exc_info=True)
            return self.error_response(
                message="Unable to retrieve top trending color/category.",
                dev_message=f"{type(e).__name__}: {e}",
            )
