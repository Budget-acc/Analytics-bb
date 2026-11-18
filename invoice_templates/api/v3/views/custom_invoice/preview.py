from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.decorators import action

from invoice_templates.v3.registry import get_definition
from invoice_templates.v3.services.configuration import migrate_configuration

from ...serializers import PreviewRequestSerializer
from ...utils import docs, messages
from ...utils.throttles import PreviewRateThrottle
from ..invoice_template.preview import PREVIEW_200, PREVIEW_400, PREVIEW_429, PREVIEW_500


@extend_schema(
    tags=[docs.TAG],
    summary="Preview a saved customized invoice",
    description=(
        "Renders a single-page PDF of one of the shop's ALREADY SAVED customized invoices with "
        "fixed sample data, and returns the PDF file.\n\n"
        "Purpose: See how a saved customized invoice looks - as saved, or with unsaved edits. "
        "Before the first Save (no customized invoice exists yet) use "
        "POST templates/{code}/preview/ with the predefined template's code or id instead.\n\n"
        "Context: Values in `configuration` are previewed on top of the saved settings without "
        "saving them; omit it to preview the saved settings. Uses the shop's real header and its "
        "Terms and Conditions for the chosen document type. Never saves anything. Rate limited to "
        "30 previews per minute per user.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- id: customized invoice id.\n"
        "Request body - Required:\n"
        "- document_type (string): booking | sale | tailor_order.\n"
        "Request body - Optional:\n"
        "- configuration (object): unsaved values to preview.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.CUSTOM_INVOICE_ID_PATH, docs.SHOP_HEADER],
    request=PreviewRequestSerializer,
    responses={
        (200, "application/pdf"): PREVIEW_200,
        400: PREVIEW_400,
        403: docs.R403,
        404: docs.R404_CUSTOM_INVOICE,
        429: PREVIEW_429,
        500: PREVIEW_500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={
            "document_type": "sale", "configuration": {"colors": {"background": "#FFFFFF"}},
        }),
        docs.INVALID_SETTINGS_EXAMPLE,
    ],
)
@action(detail=True, methods=["post"], url_path="preview", throttle_classes=[PreviewRateThrottle])
def preview_custom_invoice(self, request, pk=None):
    try:
        invoice = self.get_invoice(pk)
        if invoice is None:
            return self.invoice_not_found()

        serializer = PreviewRequestSerializer(data=request.data)
        if not serializer.is_valid():
            if "document_type" in serializer.errors:
                return self.error_response(
                    message=messages.INVALID_DOCUMENT_TYPE, dev_message=messages.INVALID_DOCUMENT_TYPE_DEV,
                )
            return self.error_response(
                message=messages.CONFIGURATION_REQUIRED, dev_message=messages.CONFIGURATION_REQUIRED_DEV,
            )

        definition = get_definition(invoice.template.code)
        saved = migrate_configuration(definition, invoice.configuration, invoice.template_version)
        raw = serializer.validated_data.get("configuration")
        if "configuration" in serializer.validated_data and raw is None:
            raw = "invalid"  # explicit null is not a configuration object
        configuration, error = self.preview_configuration(definition, raw, base=saved)
        if error is not None:
            return error
        return self.preview_pdf_response(
            definition, configuration, serializer.validated_data["document_type"], invoice.name,
        )
    except Exception:
        return self.server_error(f"Customized invoice preview failed for shop {self.shop_id}, id {pk}")
