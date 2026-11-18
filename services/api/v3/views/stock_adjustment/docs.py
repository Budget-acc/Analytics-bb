"""Shared @extend_schema pieces for the stock-adjustment endpoints.

Error lists are built from StockAdjustmentMessages, so the documented
messages are always exactly the ones the endpoints return.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes

from services.constants.stock_adjustment import VALIDATION_MSGS, StockAdjustmentMessages as M

TAG = "Service & Product Management V3"

PRODUCT_ID_PARAMETER = OpenApiParameter(
    name="product_id",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    required=True,
    description=(
        "Purpose: The product whose variants are adjusted or reported. Must belong to the active shop "
        "(X-Active-Shop-ID header) and must not be deleted. "
        "Location: URL path parameter. Format/Type: positive integer (e.g. 41). Required."
    ),
)


def _bullets(messages):
    return "\n".join(f"- {message}" for message in messages)


POST_400_MESSAGES = [
    M.INVALID_REQUEST.message,
    *(msg.message for msg in VALIDATION_MSGS),
    M.INVALID_VARIANTS.message,
    M.FABRIC_NOT_TRACKED.message,
    M.STOCK_NOT_AVAILABLE.message,
    M.FABRIC_NOT_AVAILABLE.message,
    M.STOCK_NOT_UNAVAILABLE.message,
    M.FABRIC_NOT_UNAVAILABLE.message,
    M.AMOUNT_TOO_LARGE.message,
]

COMMON_ERROR_RESPONSES = {
    401: OpenApiResponse(description=(
        "Authentication error (DRF `detail` response from the JWT layer). Exact messages include:\n"
        "- Authentication credentials were not provided.\n"
        "- Given token not valid for any token type\n"
        "- Session invalidated\n"
        "- Token invalidated by password change"
    )),
    403: OpenApiResponse(description=(
        "Permission error. Exact messages include:\n"
        "- You do not have permission to access this shop.\n"
        "- Shop does not have an active subscription."
    )),
    404: OpenApiResponse(description=f"Not found. Exact message: {M.PRODUCT_NOT_FOUND.message}"),
    500: OpenApiResponse(description=f"Unexpected error. Exact message: {M.SERVER_ERROR.message}"),
}

POST_RESPONSES = {
    200: OpenApiResponse(description=(
        "Success. Every item was applied.\n\n"
        "Response shape:\n"
        f"- message (string): \"{M.MARKED_UNAVAILABLE}\" or \"{M.RESTORED}\".\n"
        "- data.batch_id (string, UUID): Groups the history rows written by this request; use it to filter the history.\n"
        "- data.action (string): mark_unavailable or restore.\n"
        "- data.product_id (integer).\n"
        "- data.variants (array, same order as the request). Each item contains:\n"
        "  - variant_id (integer), attribute (string), reason (string | null - null when no reason was sent), "
        "note (string).\n"
        "  - quantity (integer): units moved by this request (0 if only fabric was moved).\n"
        "  - fabric_length (string, 2 dp): metres moved by this request (\"0.00\" if only units were moved).\n"
        "  - stock (integer): usable units now - the value every booking/availability API uses.\n"
        "  - unavailable_stock (integer): units out of service now.\n"
        "  - total_stock (integer): stock + unavailable_stock (physical units).\n"
        "  - available_fabric_length (string | null): usable fabric now; null if the variant does not track fabric.\n"
        "  - unavailable_fabric_length (string): fabric out of service now.\n"
        "  - total_fabric_length (string | null): available + unavailable fabric; null if the variant does not track fabric."
    )),
    400: OpenApiResponse(description=(
        "Validation or business-rule error. Nothing is written (all-or-nothing). `data` is null; `message` is the "
        "first error, for the end user. `dev_message` names the failing field (e.g. \"Field: variants[1].quantity.\"), "
        "lists unknown or duplicate variant ids, and for amount errors describes every failing variant "
        "(e.g. \"Variant 102: quantity 3 exceeds stock 1.\"). "
        "{name}, {attribute} and {available} are filled with the product name, the variant attribute "
        "(\"default\" when empty) and the current amount. Exact messages:\n" + _bullets(POST_400_MESSAGES)
    )),
    409: OpenApiResponse(description=f"Concurrent update conflict. Nothing is written. Exact message: {M.CONFLICT.message}"),
    **COMMON_ERROR_RESPONSES,
}
