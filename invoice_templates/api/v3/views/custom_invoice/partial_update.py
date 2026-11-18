from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from invoice_templates.v3.registry import get_definition
from invoice_templates.v3.services.configuration import (
    ConfigurationError, effective_configuration, validate_configuration,
)
from invoice_templates.v3.services.custom_invoices import CustomInvoiceError, update_custom_invoice

from ...serializers import UpdateCustomInvoiceRequestSerializer
from ...utils import docs, messages
from .helpers import DETAIL_EXAMPLE, DETAIL_SHAPE, default_invoice_id, invoice_detail


@extend_schema(
    tags=[docs.TAG],
    summary="Update a customized invoice (template, name and/or settings)",
    description=(
        "Switches a customized invoice to another template, renames it and/or changes its settings.\n\n"
        "Purpose: 'Save' when editing an existing customized invoice.\n\n"
        "Context: Updates are unlimited and never count toward the creation limit. Send only the "
        "values that change - e.g. {\"configuration\": {\"colors\": {\"primary\": \"#8B1E3F\"}}} "
        "changes only the primary color; every other value is kept. Sending template_code (or "
        "template_id) switches the template (e.g. classic -> simple, simple -> standard); the saved "
        "colors and font carry over and any configuration sent in the same request is applied on "
        "top. A template switch revokes the invoice's active share code (generate a new one to "
        "share the new template). If this is the default invoice, real invoices use the new "
        "template / settings immediately.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- id: customized invoice id.\n"
        "Request body - at least one of (each optional):\n"
        "- template_code (string, from GET templates/) or template_id (integer).\n"
        "- name (string, 1-60 characters, unique in the shop, case-insensitive).\n"
        "- configuration (object): {colors: {background?, primary?, text?, border?, cancelled?}, "
        "font?} - only the values to change.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.CUSTOM_INVOICE_ID_PATH, docs.SHOP_HEADER],
    request=UpdateCustomInvoiceRequestSerializer,
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact message: {messages.CUSTOM_INVOICE_UPDATED}\n\nResponse shape (data):\n" + DETAIL_SHAPE
        )),
        400: OpenApiResponse(description=(
            "Validation error. Exact messages:\n"
            + messages.bullet(
                messages.UPDATE_FIELDS_REQUIRED, messages.NAME_TAKEN, messages.INVALID_NAME,
                messages.CONFIGURATION_REQUIRED, messages.INVALID_SETTINGS,
            )
            + "\n\n" + messages.FIELD_ERRORS_DOC
        )),
        403: docs.R403,
        404: OpenApiResponse(description=(
            "Not found. Exact messages:\n"
            + messages.bullet(messages.CUSTOM_INVOICE_NOT_FOUND, messages.TEMPLATE_NOT_FOUND)
            + "\n(" + messages.TEMPLATE_NOT_FOUND + " when template_code / template_id is unknown or inactive.)"
        )),
        500: docs.R500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={
            "name": "Wedding Season 2026", "configuration": {"colors": {"primary": "#8B1E3F"}},
        }),
        OpenApiExample("Switch template", request_only=True, value={"template_code": "simple"}),
        docs.envelope_example("Success", messages.CUSTOM_INVOICE_UPDATED, data=DETAIL_EXAMPLE),
        docs.INVALID_SETTINGS_EXAMPLE,
    ],
)
def partial_update(self, request, pk=None):
    try:
        invoice = self.get_invoice(pk)
        if invoice is None:
            return self.invoice_not_found()

        serializer = UpdateCustomInvoiceRequestSerializer(data=request.data)
        if not serializer.is_valid():
            if "template_code" in serializer.errors or "template_id" in serializer.errors:
                return self.template_not_found()
            if "name" in serializer.errors:
                return self.error_response(message=messages.INVALID_NAME, dev_message=messages.INVALID_NAME_DEV)
            return self.error_response(
                message=messages.CONFIGURATION_REQUIRED, dev_message=messages.CONFIGURATION_REQUIRED_DEV,
            )
        data = serializer.validated_data
        template_ref = data.get("template_id") or (data.get("template_code") or "").strip()
        if "name" not in data and "configuration" not in data and not template_ref:
            return self.error_response(
                message=messages.UPDATE_FIELDS_REQUIRED, dev_message=messages.UPDATE_FIELDS_REQUIRED_DEV,
            )
        if "configuration" in data and not isinstance(data["configuration"], dict):
            return self.error_response(
                message=messages.CONFIGURATION_REQUIRED, dev_message=messages.CONFIGURATION_REQUIRED_DEV,
            )

        definition = get_definition(invoice.template.code)
        new_template = None
        if template_ref:
            template, new_definition = self.resolve_template(template_ref)
            if template is None:
                return self.template_not_found()
            if template.id != invoice.template_id:
                new_template = template

        clean = None
        try:
            if new_template is not None:
                # Switching template: the saved colors / font carry over (every
                # template offers the same options), plus anything sent now.
                base = effective_configuration(definition, invoice.configuration, invoice.template_version)
                definition = new_definition
                clean = validate_configuration(definition, data.get("configuration") or {}, base=base)
            elif "configuration" in data:
                clean = validate_configuration(definition, data["configuration"], base=invoice.configuration)
        except ConfigurationError as e:
            return self.field_errors_response(messages.INVALID_SETTINGS, e.errors, messages.INVALID_SETTINGS_DEV)

        try:
            update_custom_invoice(
                invoice, definition, request.user, name=data.get("name"), configuration=clean, template=new_template,
            )
        except CustomInvoiceError as e:
            return self.custom_invoice_error_response(e)

        return self.success_response(
            data=invoice_detail(invoice, default_invoice_id(self.shop_id)),
            message=messages.CUSTOM_INVOICE_UPDATED,
        )
    except Exception:
        return self.server_error(f"Customized invoice update failed for shop {self.shop_id}, id {pk}")
