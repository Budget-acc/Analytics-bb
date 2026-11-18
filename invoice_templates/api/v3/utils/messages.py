"""
Every user-facing `message` and developer `dev_message` returned by the
invoice template APIs. Views AND their swagger (extend_schema) docs read
these constants, so the documented messages are exactly what is returned.
"""
from invoice_templates.v3.registry import fields as _fields

# ---- Shared ---------------------------------------------------------------
SHOP_ACCESS_DENIED = "You are not authorized to access this shop."
ROLE_DENIED = "You don't have the permission."
NOT_ASSOCIATED = "You are not associated with this shop."
SUBSCRIPTION_INACTIVE = "Shop does not have an active subscription."
SERVER_ERROR = "Something went wrong. Please try again."
SERVER_ERROR_DEV = "Unexpected server error. See server logs for the request."

TEMPLATE_NOT_FOUND = "Invoice template not found."
TEMPLATE_NOT_FOUND_DEV = "No active invoice template exists with this code."

INVALID_SETTINGS = "Invalid template settings."
INVALID_SETTINGS_DEV = (
    "configuration failed validation; see data.validation_errors "
    "(colors.<name> / font -> messages)."
)
CONFIGURATION_REQUIRED = "Request body must contain a configuration object."
CONFIGURATION_REQUIRED_DEV = (
    "Send {\"configuration\": {\"colors\": {\"primary\": \"#RRGGBB\", ...}, \"font\": \"inter\"}}."
)

INVALID_DOCUMENT_TYPE = "Invalid document type. Allowed: booking, sale, tailor_order."
INVALID_DOCUMENT_TYPE_DEV = "document_type must be one of: booking, sale, tailor_order."

# ---- Templates (catalog) --------------------------------------------------
TEMPLATES_FETCHED = "Invoice templates fetched successfully."
TEMPLATE_FETCHED = "Invoice template fetched successfully."
PREVIEW_FAILED = "Failed to generate preview. Please try again."
PREVIEW_FAILED_DEV = "PDF rendering failed for the submitted configuration. See server logs."
PREVIEW_THROTTLED = "Too many preview requests. Please wait a moment."
PREVIEW_RESPONSE_FAILED = "Failed to prepare PDF response."
PREVIEW_RESPONSE_FAILED_DEV = "The preview PDF was generated but the file response could not be built. See server logs."

# ---- Customized invoices --------------------------------------------------
CUSTOM_INVOICES_FETCHED = "Customized invoices fetched successfully."
CUSTOM_INVOICE_FETCHED = "Customized invoice fetched successfully."
CUSTOM_INVOICE_CREATED = "Customized invoice created successfully."
CUSTOM_INVOICE_UPDATED = "Customized invoice updated successfully."
CUSTOM_INVOICE_DELETED = "Customized invoice deleted successfully."
CUSTOM_INVOICE_NOT_FOUND = "Customized invoice not found."
CUSTOM_INVOICE_NOT_FOUND_DEV = "No customized invoice with this id exists for the active shop."
TEMPLATE_CODE_REQUIRED = "template_code or template_id is required."
TEMPLATE_CODE_REQUIRED_DEV = (
    "Send {\"template_code\": \"<code from GET templates/>\"} or {\"template_id\": <id from GET templates/>}."
)
LIMIT_REACHED_TEMPLATE = "You can create up to {limit} customized invoices. Delete one to create a new one."
LIMIT_REACHED_DEV = (
    "The shop already has the maximum number of customized invoices (data.usage). "
    "Updating existing ones is unlimited; deleting one frees a slot."
)
NAME_TAKEN = "A customized invoice with this name already exists."
NAME_TAKEN_DEV = "Names are unique per shop (case-insensitive). Choose another name."
INVALID_NAME = "Invoice name must be 1-60 characters without < > { } or line breaks."
INVALID_NAME_DEV = "name must be a string of 1-60 characters after trimming."
UPDATE_FIELDS_REQUIRED = "Provide name and/or configuration to update."
UPDATE_FIELDS_REQUIRED_DEV = (
    "PATCH body must contain name and/or configuration (object). The template cannot be changed."
)


def limit_reached_message(limit=None) -> str:
    from invoice_templates.v3.services.custom_invoices import creation_limit

    return LIMIT_REACHED_TEMPLATE.format(limit=limit if limit is not None else creation_limit())


# ---- Preference -----------------------------------------------------------
PREFERENCE_FETCHED = "Invoice preferences fetched successfully."
DEFAULT_INVOICE_UPDATED = "Default invoice updated successfully."
DEFAULT_INVOICE_REQUIRED = "custom_invoice_id is required (use null for the standard invoice)."
DEFAULT_INVOICE_REQUIRED_DEV = (
    "Send {\"custom_invoice_id\": <id from GET custom-invoices/>} or {\"custom_invoice_id\": null}."
)
CUSTOMIZATION_UPDATED = "Invoice customization setting updated successfully."
ENABLED_REQUIRED = "enabled must be true or false."
ENABLED_REQUIRED_DEV = "Send {\"enabled\": true} or {\"enabled\": false} (JSON boolean)."
TERMS_UPDATED = "Terms and conditions updated successfully."
INVALID_TERMS = "Invalid terms format."
INVALID_TERMS_DEV = "See data.validation_errors (document type -> messages)."
TERMS_REQUIRED = "Provide at least one of booking, sale, tailor_order."
TERMS_REQUIRED_DEV = "Request body must contain at least one of the keys: booking, sale, tailor_order."

# ---- Sharing --------------------------------------------------------------
SHARE_CREATED = "Share code generated successfully."
SHARE_EXISTING = "Share code fetched successfully."
SHARE_FETCHED = "Share code fetched successfully."
NO_ACTIVE_SHARE_DATA = "No active share code for this customized invoice."
SHARE_REVOKED = "Share code revoked successfully."
NO_ACTIVE_SHARE = "No active share code found for this customized invoice."
NO_ACTIVE_SHARE_DEV = "There is no un-revoked share code for this customized invoice."
INVALID_SHARE_CODE = "Invalid or expired code."
INVALID_SHARE_CODE_DEV = (
    "The code is malformed, unknown, revoked, its customized invoice was deleted, "
    "or its template/source shop is no longer active."
)
SHARE_CODE_EXPIRED = "This code has expired. Ask the owner to generate a new code."
SHARE_CODE_EXPIRED_DEV = (
    "The share code is past its 24-hour validity (expires_at has passed); "
    "the source shop must call generate to get a new code."
)
SHARE_CODE_EXPIRED_ERROR_CODE = "share_code_expired"
OWN_SHARE_CODE = "You cannot import your own invoice code."
OWN_SHARE_CODE_DEV = "The share code was generated by the active shop."
SHARE_CODE_REQUIRED = "share_code is required."
SHARE_CODE_REQUIRED_DEV = "Send {\"share_code\": \"XXXX-XXXX-XXXX\"}."
SHARED_FETCHED = "Shared invoice fetched successfully."
SHARE_IMPORTED = "Customized invoice imported successfully."
SHARE_THROTTLED = "Too many attempts. Please try again later."

# ---- Field-level validation message templates (in validation_errors) -----
FIELD_ERROR_TEMPLATES = (
    _fields.ERR_UNKNOWN_FIELD,
    _fields.ERR_COLOR,
    _fields.ERR_FONT,
    _fields.ERR_COLORS_OBJECT,
)


def bullet(*messages) -> str:
    """Swagger helper: '- msg' lines."""
    return "\n".join(f"- {m}" for m in messages)


# Raised before the view runs by the shared shop / role / subscription
# permission classes (shop.api.v3.utils.shop, subscriptions...permissions).
PERMISSION_MESSAGES = (
    "Invalid Shop ID format. Must be an integer.",
    "Invalid, inactive, or deleted Shop ID provided.",
    "You do not have permission to access this shop.",
    "User is not associated with any active shop.",
    NOT_ASSOCIATED,
    ROLE_DENIED,
    "Shop has no subscription.",
    "Shop subscription has expired.",
    "Shop subscription is not active (status: <status>).",
    SUBSCRIPTION_INACTIVE,
    SHOP_ACCESS_DENIED,
)

COMMON_403 = "Permission error. Exact messages:\n" + bullet(*PERMISSION_MESSAGES)
COMMON_500 = f"Server error. Exact message: {SERVER_ERROR}"
FIELD_ERRORS_DOC = (
    "Field-level messages inside data.validation_errors (keyed colors.<name>, font, colors or the "
    "unknown key) use these exact templates ({key}/{options} are filled in):\n"
    + bullet(*FIELD_ERROR_TEMPLATES)
)
