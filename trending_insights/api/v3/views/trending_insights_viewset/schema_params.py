from drf_spectacular.utils import OpenApiParameter, OpenApiTypes

MONTH_PARAM = OpenApiParameter(
    name="month",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Purpose: Selects which calendar month's data the analytics are computed over "
        "(month/year filter, used by older app versions). "
        "Location: query parameter. "
        "Format/Type: integer, 1-12 (e.g. 9 for September). "
        "Optional - send month + year OR start_date + end_date. When month or year is sent, "
        "the month/year filter is used (and both are then required) and start_date/end_date "
        "are ignored."
    ),
)

YEAR_PARAM = OpenApiParameter(
    name="year",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Purpose: Selects which calendar year's data the analytics are computed over, together "
        "with month. "
        "Location: query parameter. "
        "Format/Type: integer, between 2000 and 2100 (e.g. 2026). "
        "Optional - required together with month when using the month/year filter."
    ),
)

RANGE_START_DATE_PARAM = OpenApiParameter(
    name="start_date",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Purpose: Start of a custom date range (inclusive) the analytics are computed over - "
        "the date-range filter used by newer app versions instead of month/year. "
        "Location: query parameter. "
        "Format/Type: date string - either DD-MM-YYYY (e.g. 10-08-2026) or YYYY-MM-DD "
        "(e.g. 2026-08-10). "
        "Optional - used only when month and year are not sent (or are empty/null); then "
        "start_date and end_date are both required, start_date must be <= end_date, and the "
        "range must not exceed 90 days (inclusive)."
    ),
)

RANGE_END_DATE_PARAM = OpenApiParameter(
    name="end_date",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Purpose: End of a custom date range (inclusive) the analytics are computed over. "
        "Location: query parameter. "
        "Format/Type: date string - either DD-MM-YYYY (e.g. 02-09-2026) or YYYY-MM-DD "
        "(e.g. 2026-09-02). "
        "Optional - required together with start_date when using the date-range filter; must "
        "be >= start_date and the range must not exceed 90 days (inclusive)."
    ),
)

PAGE_PARAM = OpenApiParameter(
    name="page",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Purpose: Selects which page of trending products to return, for the 'View More' flow "
        "beyond the top 5 shown on the main dashboard. "
        "Location: query parameter. "
        "Format/Type: integer, 1 or greater. "
        "Optional. Default: 1. 20 results are returned per page."
    ),
)

TYPE_PARAM = OpenApiParameter(
    name="type",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Purpose: Restricts the response to only the top trending color or only the top "
        "trending category, instead of both. "
        "Location: query parameter. "
        "Format/Type: string, one of 'color' or 'category'. "
        "Optional. If omitted, both top_trending_color and top_trending_category are returned."
    ),
)

START_DATE_PARAM = OpenApiParameter(
    name="start_date",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=True,
    description=(
        "Purpose: Start of the date range to check for Booking/Sale activity - the lower bound "
        "used to count each product's orders (unused = 0, low usage = 1-5). "
        "Location: query parameter. "
        "Format/Type: date string - either YYYY-MM-DD (e.g. 2026-08-01) or DD-MM-YYYY (e.g. "
        "01-08-2026). "
        "Required. Must be <= end_date, and the range (inclusive) must not exceed 180 days."
    ),
)

END_DATE_PARAM = OpenApiParameter(
    name="end_date",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=True,
    description=(
        "Purpose: End of the date range to check for Booking/Sale activity - the upper bound "
        "(inclusive) used to count each product's orders. "
        "Location: query parameter. "
        "Format/Type: date string - either YYYY-MM-DD (e.g. 2026-09-01) or DD-MM-YYYY (e.g. "
        "01-09-2026). "
        "Required. Must be >= start_date, and the range must not exceed 180 days."
    ),
)

MONTH_YEAR_ERRORS = (
    "month and year are required.; "
    "month and year must be valid integers.; "
    "month must be between 1 and 12.; "
    "year is out of range."
)

def date_range_errors(max_days):
    """Exact get_date_range() messages for a given max range."""
    return (
        "start_date and end_date are required.; "
        "start_date and end_date must be valid dates in YYYY-MM-DD or DD-MM-YYYY format.; "
        "end_date must be greater than or equal to start_date.; "
        f"Date range cannot exceed {max_days} days."
    )


DATE_RANGE_ERRORS = date_range_errors(180)

# Endpoints accepting either month/year or start_date/end_date (max 90 days).
PERIOD_ERRORS = f"{MONTH_YEAR_ERRORS}; {date_range_errors(90)}"

PERIOD_INPUTS_DOC = (
    "Accepted inputs (send ONE of the two filters):\n"
    "Query parameters - month/year filter (older app versions):\n"
    "- month (integer, 1-12) and year (integer, 2000-2100): the calendar month to analyze. "
    "Used whenever month or year is sent; both are then required.\n\n"
    "Query parameters - date-range filter (newer app versions):\n"
    "- start_date (DD-MM-YYYY or YYYY-MM-DD): start of the range, inclusive.\n"
    "- end_date (DD-MM-YYYY or YYYY-MM-DD): end of the range, inclusive. Must be >= start_date; "
    "the range must not exceed 90 days. Used only when month and year are not sent (or are "
    "empty/null).\n"
    "If neither filter is sent the request is rejected with 'month and year are required.'.\n\n"
)

PERIOD_RESPONSE_DOC = (
    "- month, year (integer): the requested month/year (month/year filter), or the month/year of "
    "start_date (date-range filter).\n"
    "- start_date, end_date (string, YYYY-MM-DD): the analyzed period, inclusive - the month's "
    "first and last day for the month/year filter, or the requested range for the date-range "
    "filter.\n"
)

TASK_ID_PARAM = OpenApiParameter(
    name="task_id",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    required=True,
    description=(
        "Purpose: Identifies the Excel generation task to check, as returned by "
        "unused-products/excel-report/. "
        "Location: URL path parameter. "
        "Format/Type: string (UUID, e.g. 5b0c1f2e-8a1d-4c3e-9b7a-2f6d1e0c9a11). "
        "Required."
    ),
)
