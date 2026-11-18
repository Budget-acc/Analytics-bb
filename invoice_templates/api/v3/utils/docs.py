"""Shared drf-spectacular building blocks for the invoice template APIs."""
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse
from drf_spectacular.types import OpenApiTypes

from . import messages

TAG = "Invoice Template Management V3"

AUTH_NOTE = (
    "Authentication: JWT Bearer token. The active shop is taken from the X-Active-Shop-ID "
    "header (falls back to the user's first active shop). Only the shop OWNER can call this "
    "API, and the shop must have an active subscription."
)

SHOP_HEADER = OpenApiParameter(
    name="X-Active-Shop-ID",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        "Purpose: Selects which of the user's shops the request acts on. Every read and write "
        "is scoped to this shop. "
        "Location: request header. "
        "Format/Type: integer shop id (e.g. 12). "
        "Optional. If omitted, the user's first active shop is used."
    ),
)

TEMPLATE_CODE_PATH = OpenApiParameter(
    name="code",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    required=True,
    description=(
        "Purpose: Identifies the predefined invoice template (not a shop's customized invoice). "
        "Location: URL path parameter. "
        "Format/Type: the template's code or its numeric id from GET templates/ "
        "(e.g. classic or 2). "
        "Required."
    ),
)

CUSTOM_INVOICE_ID_PATH = OpenApiParameter(
    name="id",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    required=True,
    description=(
        "Purpose: Identifies one of the active shop's customized invoices. "
        "Location: URL path parameter. "
        "Format/Type: integer id from GET custom-invoices/ (e.g. 42). "
        "Required. Ids of other shops' invoices are treated as not found."
    ),
)

SHARE_CUSTOM_INVOICE_ID_PATH = OpenApiParameter(
    name="custom_invoice_id",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    required=True,
    description=(
        "Purpose: The customized invoice whose name and settings are shared. "
        "Location: URL path parameter. "
        "Format/Type: integer id from GET custom-invoices/ (e.g. 42). "
        "Required."
    ),
)

SHARE_CODE_PATH = OpenApiParameter(
    name="share_code",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    required=True,
    description=(
        "Purpose: The share code to look up. "
        "Location: URL path parameter. "
        "Format/Type: string - 12 characters from 23456789ABCDEFGHJKMNPQRSTUVWXYZ, "
        "case-insensitive, hyphens ignored (e.g. K7QM-2XRP-9HTD). "
        "Required."
    ),
)

R403 = OpenApiResponse(description=messages.COMMON_403)
R500 = OpenApiResponse(description=messages.COMMON_500)
R404_TEMPLATE = OpenApiResponse(
    description=f"Not found. Exact message: {messages.TEMPLATE_NOT_FOUND}"
)
R404_CUSTOM_INVOICE = OpenApiResponse(
    description=f"Not found. Exact message: {messages.CUSTOM_INVOICE_NOT_FOUND}"
)


def envelope_example(name, message, data=None, dev_message=None, status="success", status_codes=None):
    value = {"status": status, "message": message, "data": data}
    if dev_message:
        value["dev_message"] = dev_message
    return OpenApiExample(name, value=value, response_only=True, status_codes=status_codes or ["200"])


INVALID_SETTINGS_EXAMPLE = envelope_example(
    "Invalid settings",
    messages.INVALID_SETTINGS,
    data={"validation_errors": {
        "colors.primary": ["'colors.primary' must be a color in #RRGGBB format."],
        "font": ["'font' must be one of: inter, noto_sans_malayalam, dejavu_sans, cormorant_garamond, roboto_mono."],
        "colors.accent": ["'colors.accent' is not a supported setting."],
    }},
    dev_message=messages.INVALID_SETTINGS_DEV,
    status="validation_error",
    status_codes=["400"],
)
