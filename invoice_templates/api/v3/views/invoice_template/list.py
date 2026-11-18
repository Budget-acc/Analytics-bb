from drf_spectacular.utils import OpenApiResponse, extend_schema

from invoice_templates.models import InvoiceTemplate
from invoice_templates.v3.registry import REGISTRY
from invoice_templates.v3.registry.fields import font_options

from ...utils import docs, messages
from .helpers import OPTIONS_EXAMPLE


@extend_schema(
    tags=[docs.TAG],
    summary="List predefined invoice templates",
    description=(
        "Returns every active predefined invoice template a shop can build a customized "
        "invoice from, with each template's default settings and the fonts the shop can choose.\n\n"
        "Purpose: Drives the 'choose a template' gallery when creating a customized invoice - "
        "everything the gallery shows (name, description, default colors and font) comes in "
        "this one call.\n\n"
        "Context: Called when the user starts creating a customized invoice. The user then "
        "opens a template (GET templates/{code}/), optionally previews it "
        "(POST templates/{code}/preview/) and creates the customized invoice "
        "(POST custom-invoices/). The shop's own customized invoices are listed by "
        "GET custom-invoices/.\n\n"
        "Accepted inputs: no path, query or body parameters.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact message: {messages.TEMPLATES_FETCHED}\n\n"
            "Response shape (data):\n"
            "- templates (array), each item:\n"
            "  - id (integer), code (string): either one identifies the template in templates/{code}/... "
            "URLs and when creating a customized invoice.\n"
            "  - name (string), description (string), version (integer).\n"
            "  - defaults (object): the template's default settings - colors {background, primary, "
            "text, border, cancelled} (each \"#RRGGBB\") and font (string, one of fonts[].value).\n"
            "- fonts (array): fonts the shop can choose for any template - [{value, label}].\n"
            "The invoice logo always comes from the shop's own logo (shop profile)."
        )),
        403: docs.R403,
        500: docs.R500,
    },
    examples=[docs.envelope_example("Success", messages.TEMPLATES_FETCHED, data={
        "templates": [
            {"id": 1, "code": "standard", "name": "Standard", "description": "The classic BookieBuddy invoice...",
             "version": 2, "defaults": {
                 "colors": {"background": "#FFFFFF", "primary": "#6C5CE7", "text": "#374151",
                            "border": "#D9D9D9", "cancelled": "#EF4444"},
                 "font": "inter",
             }},
            {"id": 2, "code": "classic", "name": "Classic", "description": "Elegant cream invoice...",
             "version": 2, "defaults": OPTIONS_EXAMPLE["defaults"]},
            {"id": 3, "code": "simple", "name": "Simple", "description": "Clean white invoice...",
             "version": 1, "defaults": {
                 "colors": {"background": "#FFFFFF", "primary": "#7C3AED", "text": "#1F2937",
                            "border": "#E5E7EB", "cancelled": "#EF4444"},
                 "font": "inter",
             }},
        ],
        "fonts": OPTIONS_EXAMPLE["fonts"],
    })],
)
def list(self, request):
    try:
        # code__in=REGISTRY guarantees every row has a code definition (defaults come from code, no extra query).
        templates = InvoiceTemplate.objects.filter(is_active=True, code__in=REGISTRY).order_by("display_order", "id")
        data = {
            "templates": [
                {
                    "id": template.id,
                    "code": template.code,
                    "name": template.name,
                    "description": template.description,
                    "version": template.version,
                    "defaults": REGISTRY[template.code].defaults,
                }
                for template in templates
            ],
            "fonts": font_options(),
        }
        return self.success_response(data=data, message=messages.TEMPLATES_FETCHED)
    except Exception:
        return self.server_error(f"Invoice template list failed for shop {self.shop_id}")
