from drf_spectacular.utils import OpenApiResponse, extend_schema

from ...utils import docs, messages
from .helpers import preference_payload

PREFERENCE_SHAPE = (
    "Response shape (data):\n"
    "- is_customization_enabled (boolean): the shop's switch for customized invoices.\n"
    "- global_customization_enabled (boolean): the platform-wide switch.\n"
    "- uses_customized_invoices (boolean): both switches on - real invoices use the default "
    "customized invoice; otherwise they use the existing invoice design.\n"
    "- default_custom_invoice (object | null): {id, name, template_code} of the customized invoice "
    "used for real invoices; null = the standard invoice with default settings.\n"
    "- effective_template_code (string): template real invoices are rendered with (standard when "
    "no default is selected).\n"
    "- terms (object): booking / sale / tailor_order -> array of strings | null - the "
    "document-specific terms the shop set (null = not set).\n"
    "- effective_terms (object): booking / sale / tailor_order -> array of strings actually "
    "printed on that invoice type: the shop's terms for that type, else the default terms of "
    "that type.\n"
    "- updated_at (string \"YYYY-MM-DD HH:MM:SS\", IST | null)."
)

PREFERENCE_EXAMPLE_DATA = {
    "is_customization_enabled": True,
    "global_customization_enabled": True,
    "uses_customized_invoices": True,
    "default_custom_invoice": {"id": 42, "name": "Wedding Season", "template_code": "classic"},
    "effective_template_code": "classic",
    "terms": {"booking": ["No refund after cancellation."], "sale": None, "tailor_order": None},
    "effective_terms": {
        "booking": ["No refund after cancellation."],
        "sale": [
            "Damage to the product after delivery will not be our responsibility.",
            "Refund or replacement will not be provided after the product is sold.",
            "Verify all products before leaving the shop.",
            "Products once sold will not be taken back or exchanged.",
        ],
        "tailor_order": [
            "Alterations must be claimed within 7 days of delivery.",
            "No refund after work has commenced.",
            "Ensure measurements are confirmed before finalising the order.",
            "Security deposit (if applicable) will be refunded after delivery.",
        ],
    },
    "updated_at": "2026-10-15 10:12:44",
}


@extend_schema(
    tags=[docs.TAG],
    summary="Get the shop's invoice preferences",
    description=(
        "Returns the active shop's default customized invoice, the customized-invoice switch and "
        "the Terms and Conditions for each document type.\n\n"
        "Purpose: Populates the invoice settings screen (default invoice badge, on/off toggle, "
        "Booking / Sale / Custom Work terms editors).\n\n"
        "Context: Read-only; safe to call whenever the screen opens.\n\n"
        "Accepted inputs: no path, query or body parameters.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=f"Success. Exact message: {messages.PREFERENCE_FETCHED}\n\n" + PREFERENCE_SHAPE),
        403: docs.R403,
        500: docs.R500,
    },
    examples=[docs.envelope_example("Success", messages.PREFERENCE_FETCHED, data=PREFERENCE_EXAMPLE_DATA)],
)
def get_preference(self, request):
    try:
        return self.success_response(data=preference_payload(self.shop_id), message=messages.PREFERENCE_FETCHED)
    except Exception:
        return self.server_error(f"Invoice preference fetch failed for shop {self.shop_id}")
