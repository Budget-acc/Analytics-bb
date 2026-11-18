"""Limits and messages for the product stock-adjustment API.

Every message lives here so the views, the service and the @extend_schema
documentation all use exactly the same text. `message` is shown to the end
user; `dev_message` is for frontend developers. Neither ever carries an
internal exception text.
"""
from decimal import Decimal
from typing import NamedTuple

MAX_ADJUSTMENT_ITEMS = 100
MAX_NOTE_LENGTH = 500
# Column limits: PositiveIntegerField and DecimalField(max_digits=10, decimal_places=2).
MAX_STOCK_VALUE = 2147483647
MAX_FABRIC_LENGTH_VALUE = Decimal("99999999.99")

ACTIVITY_LOG_ACTION = "stock_adjusted"


class Msg(NamedTuple):
    message: str
    dev_message: str


class StockAdjustmentMessages:
    # Lookup / request shape
    PRODUCT_NOT_FOUND = Msg(
        "Product not found.",
        "No active product with this id exists in the current shop.",
    )
    INVALID_REQUEST = Msg(
        "Please check the details and try again.",
        "Request body must be a JSON object with action and variants (reason and note are optional).",
    )
    INVALID_ACTION = Msg(
        "Please choose a valid action.",
        "action is required and must be one of: mark_unavailable, restore.",
    )
    INVALID_REASON = Msg(
        "Please choose a valid reason.",
        "reason is optional; when sent it must be one of: damage, maintenance, other (or null).",
    )
    NOTE_TOO_LONG = Msg(
        "Note can be up to 500 characters.",
        "note must be a string of at most 500 characters.",
    )
    NOTE_REQUIRED_FOR_OTHER = Msg(
        "Please add a note when the reason is Other.",
        "note is required (1-500 chars) when the effective reason of an item is 'other'.",
    )
    VARIANTS_REQUIRED = Msg(
        "Please select at least one variant.",
        "variants must be a non-empty array of objects.",
    )
    TOO_MANY_VARIANTS = Msg(
        "You can adjust up to 100 variants at a time.",
        "variants array exceeds 100 items.",
    )
    DUPLICATE_VARIANTS = Msg(
        "Each variant can be added only once.",
        "Duplicate variant_id values in variants: {ids}.",
    )
    INVALID_VARIANT_ID = Msg(
        "Please select a valid variant.",
        "variant_id is required and must be a positive integer.",
    )
    AMOUNT_REQUIRED = Msg(
        "Please enter a quantity or fabric length.",
        "Each item needs quantity (integer > 0) and/or fabric_length (decimal > 0).",
    )
    INVALID_QUANTITY = Msg(
        "Quantity must be greater than 0.",
        "quantity must be a positive integer.",
    )
    INVALID_FABRIC_LENGTH = Msg(
        "Fabric length must be greater than 0.",
        "fabric_length must be a positive decimal with up to 2 decimal places and at most 8 whole digits.",
    )

    # Business rules (checked against locked rows)
    INVALID_VARIANTS = Msg(
        "Some selected variants are not valid for this product.",
        "variant_id values not found under this product (unknown, other product/shop, or deleted): {ids}.",
    )
    FABRIC_NOT_TRACKED = Msg(
        "{name} ({attribute}) does not track fabric length.",
        "Variant {variant_id} has fabric_length=null; send quantity only.",
    )
    STOCK_NOT_AVAILABLE = Msg(
        "Only {available} unit(s) of {name} ({attribute}) are available.",
        "Variant {variant_id}: quantity {requested} exceeds stock {available}.",
    )
    FABRIC_NOT_AVAILABLE = Msg(
        "Only {available} m of {name} ({attribute}) is available.",
        "Variant {variant_id}: fabric_length {requested} exceeds fabric_length {available}.",
    )
    STOCK_NOT_UNAVAILABLE = Msg(
        "Only {available} unit(s) of {name} ({attribute}) are unavailable.",
        "Variant {variant_id}: quantity {requested} exceeds unavailable_stock {available}.",
    )
    FABRIC_NOT_UNAVAILABLE = Msg(
        "Only {available} m of {name} ({attribute}) is unavailable.",
        "Variant {variant_id}: fabric_length {requested} exceeds unavailable_fabric_length {available}.",
    )
    AMOUNT_TOO_LARGE = Msg(
        "The resulting stock for {name} ({attribute}) is too large.",
        "Variant {variant_id}: the resulting stock or fabric length exceeds the column limit.",
    )

    # Infrastructure
    CONFLICT = Msg(
        "This item is being updated by someone else. Please try again.",
        "Concurrent update conflict on the variant rows; retry the request.",
    )
    SERVER_ERROR = Msg(
        "Something went wrong. Please try again.",
        "Unexpected server error while processing the stock adjustment; check server logs.",
    )

    # History filters
    INVALID_FILTERS = Msg(
        "Please check the filters and try again.",
        "Invalid query parameter(s): {fields}. variant_id must be a positive integer; action/reason must be "
        "valid choices; batch_id must be a UUID.",
    )

    # Unavailable-stock product list
    INVALID_SERVICE_ID = Msg(
        "Please select a valid service.",
        "shop_service_id must be a positive integer.",
    )
    SERVICE_NOT_FOUND = Msg(
        # Same text as the existing product list API.
        "The selected service is not available or inactive.",
        "No active, non-deleted shop service with this id exists in the current shop.",
    )

    # Success
    MARKED_UNAVAILABLE = "Stock marked as unavailable."
    RESTORED = "Stock restored to available."
    HISTORY_RETRIEVED = "Stock adjustment history retrieved."
    SUMMARY_RETRIEVED = "Stock summary retrieved."


# Request-validation messages raised by the serializers, mapped back to their
# dev_message so the view can return both.
VALIDATION_MSGS = (
    StockAdjustmentMessages.INVALID_ACTION,
    StockAdjustmentMessages.INVALID_REASON,
    StockAdjustmentMessages.NOTE_TOO_LONG,
    StockAdjustmentMessages.NOTE_REQUIRED_FOR_OTHER,
    StockAdjustmentMessages.VARIANTS_REQUIRED,
    StockAdjustmentMessages.TOO_MANY_VARIANTS,
    StockAdjustmentMessages.DUPLICATE_VARIANTS,
    StockAdjustmentMessages.INVALID_VARIANT_ID,
    StockAdjustmentMessages.AMOUNT_REQUIRED,
    StockAdjustmentMessages.INVALID_QUANTITY,
    StockAdjustmentMessages.INVALID_FABRIC_LENGTH,
)
DEV_MESSAGE_BY_MESSAGE = {msg.message: msg.dev_message for msg in VALIDATION_MSGS}
