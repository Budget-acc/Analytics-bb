from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.decorators import action

from ...serializers import PreviewRequestSerializer
from ...utils import docs, messages
from ...utils.throttles import PreviewRateThrottle

PREVIEW_200 = OpenApiResponse(
    response=OpenApiTypes.BINARY,
    description=(
        "Success. The body is the PDF file itself (not JSON), returned as a download - same format "
        "as the booking/sale invoice download APIs. Headers: Content-Type: application/pdf; "
        "Content-Disposition: attachment; filename=\"Invoice_Preview_<name>_<Document_Type>.pdf\" "
        "(e.g. Invoice_Preview_Classic_Booking.pdf); Content-Length: <bytes>; Cache-Control: no-store. "
        "Always exactly one page. Errors are returned as the usual JSON envelope."
    ),
)
PREVIEW_400 = OpenApiResponse(description=(
    "Validation error (JSON envelope). Exact messages:\n"
    + messages.bullet(messages.INVALID_DOCUMENT_TYPE, messages.CONFIGURATION_REQUIRED, messages.INVALID_SETTINGS)
    + "\n\n" + messages.FIELD_ERRORS_DOC
))
PREVIEW_429 = OpenApiResponse(description=f"Rate limited. Exact message: {messages.PREVIEW_THROTTLED}")
PREVIEW_500 = OpenApiResponse(description=(
    "Server error. Exact messages:\n"
    + messages.bullet(messages.PREVIEW_FAILED, messages.PREVIEW_RESPONSE_FAILED, messages.SERVER_ERROR)
))


@extend_schema(
    tags=[docs.TAG],
    summary="Preview a predefined template (before creating a customized invoice)",
    description=(
        "Renders a single-page PDF of a predefined template with the submitted settings and "
        "fixed sample data, and returns the PDF file.\n\n"
        "Purpose: Lets the user see how a template looks while choosing and customizing it, "
        "before creating the customized invoice.\n\n"
        "Context: The preview uses the shop's real header (name, address, phones, GST, logo) and "
        "its Terms and Conditions for the chosen document type; customer, items and totals are "
        "sample data. It NEVER saves anything. For an existing customized invoice use "
        "POST custom-invoices/{id}/preview/. Rate limited to 30 previews per minute per user.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- code: the predefined template's code or id from GET templates/ (e.g. classic or 2). "
        "NOT a customized invoice id - no customized invoice is needed for this preview.\n"
        "Request body - Required:\n"
        "- document_type (string): booking | sale | tailor_order.\n"
        "Request body - Optional:\n"
        "- configuration (object): settings to preview. If omitted, the template defaults are previewed.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.TEMPLATE_CODE_PATH, docs.SHOP_HEADER],
    request=PreviewRequestSerializer,
    responses={
        (200, "application/pdf"): PREVIEW_200,
        400: PREVIEW_400,
        403: docs.R403,
        404: docs.R404_TEMPLATE,
        429: PREVIEW_429,
        500: PREVIEW_500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={
            "document_type": "booking",
            "configuration": {"colors": {"background": "#FFF8F0", "primary": "#8B1E3F"}, "font": "inter"},
        }),
        docs.INVALID_SETTINGS_EXAMPLE,
    ],
)
@action(detail=True, methods=["post"], url_path="preview", throttle_classes=[PreviewRateThrottle])
def preview(self, request, code=None):
    try:
        template, definition = self.resolve_template(code)
        if template is None:
            return self.template_not_found()

        serializer = PreviewRequestSerializer(data=request.data)
        if not serializer.is_valid():
            if "document_type" in serializer.errors:
                return self.error_response(
                    message=messages.INVALID_DOCUMENT_TYPE, dev_message=messages.INVALID_DOCUMENT_TYPE_DEV,
                )
            return self.error_response(
                message=messages.CONFIGURATION_REQUIRED, dev_message=messages.CONFIGURATION_REQUIRED_DEV,
            )

        raw = serializer.validated_data.get("configuration")
        if "configuration" in serializer.validated_data and raw is None:
            raw = "invalid"  # explicit null is not a configuration object
        configuration, error = self.preview_configuration(definition, raw)
        if error is not None:
            return error
        return self.preview_pdf_response(
            definition, configuration, serializer.validated_data["document_type"], template.name,
        )
    except Exception:
        return self.server_error(f"Invoice template preview failed for shop {self.shop_id}, code {code}")
