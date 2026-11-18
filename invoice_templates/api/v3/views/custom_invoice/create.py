from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status

from invoice_templates.v3.services.configuration import ConfigurationError, validate_configuration
from invoice_templates.v3.services.custom_invoices import CustomInvoiceError, create_custom_invoice

from ...serializers import CreateCustomInvoiceRequestSerializer
from ...utils import docs, messages
from .helpers import DETAIL_EXAMPLE, DETAIL_SHAPE, USAGE_SHAPE, default_invoice_id, invoice_detail


@extend_schema(
    tags=[docs.TAG],
    summary="Create a customized invoice",
    description=(
        "Creates a new customized invoice for the active shop from a predefined template, with "
        "an optional name and settings.\n\n"
        "Purpose: 'Save' on the create-invoice screen after the user picked a template, "
        "customized it and previewed it.\n\n"
        "Context: Each shop can create at most 4 customized invoices; the 5th is rejected until "
        "one is deleted. The limit is per shop - other shops are not affected. Several "
        "customized invoices may use the same template. The name only helps the shop identify "
        "the invoice in the app (it is not printed); when omitted it defaults to the template "
        "name, made unique (\"Classic\", \"Classic 2\"). Creating does NOT change the default "
        "invoice (use PUT preference/default/).\n\n"
        "Accepted inputs:\n"
        "Request body - Required (one of):\n"
        "- template_code (string): code from GET templates/, or\n"
        "- template_id (integer): id from GET templates/.\n"
        "Request body - Optional:\n"
        "- name (string, 1-60 characters, unique in the shop, case-insensitive).\n"
        "- configuration (object): {colors: {background?, primary?, text?, border?, cancelled?}, "
        "font?}. Values left out use the template defaults. Default {}.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    request=CreateCustomInvoiceRequestSerializer,
    responses={
        201: OpenApiResponse(description=(
            f"Created. Exact message: {messages.CUSTOM_INVOICE_CREATED}\n\n"
            "Response shape (data):\n" + DETAIL_SHAPE + "\n" + USAGE_SHAPE
        )),
        400: OpenApiResponse(description=(
            "Validation error. Exact messages:\n"
            + messages.bullet(
                messages.TEMPLATE_CODE_REQUIRED, messages.limit_reached_message(), messages.NAME_TAKEN,
                messages.INVALID_NAME, messages.CONFIGURATION_REQUIRED, messages.INVALID_SETTINGS,
            )
            + "\n\nWhen the limit is reached, data.usage carries {used, limit, remaining}.\n\n"
            + messages.FIELD_ERRORS_DOC
        )),
        403: docs.R403,
        404: docs.R404_TEMPLATE,
        500: docs.R500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={
            "template_code": "classic", "name": "Wedding Season",
            "configuration": {"colors": {"background": "#FFF8F0", "primary": "#8B1E3F"}, "font": "inter"},
        }),
        docs.envelope_example("Created", messages.CUSTOM_INVOICE_CREATED, status_codes=["201"], data={
            **DETAIL_EXAMPLE, "usage": {"used": 2, "limit": 4, "remaining": 2},
        }),
        docs.envelope_example(
            "Limit reached", messages.limit_reached_message(), status="error", status_codes=["400"],
            data={"usage": {"used": 4, "limit": 4, "remaining": 0}}, dev_message=messages.LIMIT_REACHED_DEV,
        ),
        docs.INVALID_SETTINGS_EXAMPLE,
    ],
)
def create(self, request):
    try:
        serializer = CreateCustomInvoiceRequestSerializer(data=request.data)
        if not serializer.is_valid():
            if "template_code" in serializer.errors or "template_id" in serializer.errors:
                return self.error_response(
                    message=messages.TEMPLATE_CODE_REQUIRED, dev_message=messages.TEMPLATE_CODE_REQUIRED_DEV,
                )
            if "name" in serializer.errors:
                return self.error_response(message=messages.INVALID_NAME, dev_message=messages.INVALID_NAME_DEV)
            return self.error_response(
                message=messages.CONFIGURATION_REQUIRED, dev_message=messages.CONFIGURATION_REQUIRED_DEV,
            )
        data = serializer.validated_data

        template, definition = self.resolve_template(data.get("template_id") or data["template_code"].strip())
        if template is None:
            return self.template_not_found()

        raw = data.get("configuration", {})
        if not isinstance(raw, dict):
            return self.error_response(
                message=messages.CONFIGURATION_REQUIRED, dev_message=messages.CONFIGURATION_REQUIRED_DEV,
            )
        try:
            clean = validate_configuration(definition, raw)
        except ConfigurationError as e:
            return self.field_errors_response(messages.INVALID_SETTINGS, e.errors, messages.INVALID_SETTINGS_DEV)

        try:
            invoice, shop_usage = create_custom_invoice(
                self.shop_id, template, definition, clean, request.user, name=data.get("name"),
            )
        except CustomInvoiceError as e:
            return self.custom_invoice_error_response(e)

        return self.success_response(
            data={**invoice_detail(invoice, default_invoice_id(self.shop_id)), "usage": shop_usage},
            message=messages.CUSTOM_INVOICE_CREATED,
            http_status=status.HTTP_201_CREATED,
        )
    except Exception:
        return self.server_error(f"Customized invoice create failed for shop {self.shop_id}")
