from drf_spectacular.utils import OpenApiResponse, extend_schema

from ...utils import docs, messages
from .helpers import OPTIONS_EXAMPLE, OPTIONS_SHAPE, template_options_payload


@extend_schema(
    tags=[docs.TAG],
    summary="Get a template's default settings and font list",
    description=(
        "Returns one predefined template's default customization and the fonts a shop can pick.\n\n"
        "Purpose: Initialises the customization screen. Every template offers the same six "
        "options - background color, primary color, text color, border & line color, cancelled "
        "color and font; each template has its own defaults. Layout, font sizes, labels and all "
        "other design details are fixed by the template.\n\n"
        "Context: Called when the user picks a template to create a customized invoice from. "
        "The invoice logo is always the shop's own logo (shop profile).\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- code: template code or id (e.g. classic or 2).\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.TEMPLATE_CODE_PATH, docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact message: {messages.TEMPLATE_FETCHED}\n\n"
            "Response shape (data):\n"
            "- id (integer), code, name, description (string), version (integer).\n" + OPTIONS_SHAPE
        )),
        403: docs.R403,
        404: docs.R404_TEMPLATE,
        500: docs.R500,
    },
    examples=[docs.envelope_example("Success", messages.TEMPLATE_FETCHED, data={
        "id": 2, "code": "classic", "name": "Classic", "description": "Elegant cream invoice...", "version": 2,
        **OPTIONS_EXAMPLE,
    })],
)
def retrieve(self, request, code=None):
    try:
        template, definition = self.resolve_template(code)
        if template is None:
            return self.template_not_found()
        data = {
            "id": template.id,
            "code": template.code,
            "name": template.name,
            "description": template.description,
            "version": definition.version,
            **template_options_payload(definition),
        }
        return self.success_response(data=data, message=messages.TEMPLATE_FETCHED)
    except Exception:
        return self.server_error(f"Invoice template retrieve failed for shop {self.shop_id}, code {code}")
