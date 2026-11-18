from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.decorators import action

from invoice_templates.v3.services.custom_invoices import get_shop_invoice
from invoice_templates.v3.services.preferences import set_default_custom_invoice

from ...serializers import SetDefaultInvoiceRequestSerializer
from ...utils import docs, messages
from .get_preference import PREFERENCE_EXAMPLE_DATA, PREFERENCE_SHAPE
from .helpers import preference_payload


@extend_schema(
    tags=[docs.TAG],
    summary="Set the shop's default invoice",
    description=(
        "Selects which of the active shop's customized invoices is used for its real Booking, "
        "Sale and Custom Work invoices, or goes back to the standard invoice.\n\n"
        "Purpose: 'Set as default' on a customized invoice.\n\n"
        "Context: A shop has exactly one default; selecting another replaces it. Selecting a "
        "customized invoice also switches customized invoices on for the shop. null goes back to "
        "the standard invoice with default settings (the existing look). No customized invoice "
        "is changed or deleted. Customized invoices also require the platform-wide switch to be "
        "on - see global_customization_enabled in the response.\n\n"
        "Accepted inputs:\n"
        "Request body - Required:\n"
        "- custom_invoice_id (integer | null): id from GET custom-invoices/, or null.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    request=SetDefaultInvoiceRequestSerializer,
    responses={
        200: OpenApiResponse(description=f"Success. Exact message: {messages.DEFAULT_INVOICE_UPDATED}\n\n" + PREFERENCE_SHAPE),
        400: OpenApiResponse(description=f"Validation error. Exact message: {messages.DEFAULT_INVOICE_REQUIRED}"),
        403: docs.R403,
        404: OpenApiResponse(description=(
            f"Not found. Exact message: {messages.CUSTOM_INVOICE_NOT_FOUND} "
            "(unknown id, or an id belonging to another shop)"
        )),
        500: docs.R500,
    },
    examples=[
        OpenApiExample("Request", request_only=True, value={"custom_invoice_id": 42}),
        OpenApiExample("Back to standard", request_only=True, value={"custom_invoice_id": None}),
        docs.envelope_example("Success", messages.DEFAULT_INVOICE_UPDATED, data=PREFERENCE_EXAMPLE_DATA),
    ],
)
@action(detail=False, methods=["put"], url_path="default")
def set_default(self, request):
    try:
        serializer = SetDefaultInvoiceRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return self.error_response(
                message=messages.DEFAULT_INVOICE_REQUIRED, dev_message=messages.DEFAULT_INVOICE_REQUIRED_DEV,
            )
        invoice_id = serializer.validated_data["custom_invoice_id"]
        invoice = None
        if invoice_id is not None:
            invoice = get_shop_invoice(self.shop_id, invoice_id)
            if invoice is None:
                return self.not_found_response(
                    message=messages.CUSTOM_INVOICE_NOT_FOUND, dev_message=messages.CUSTOM_INVOICE_NOT_FOUND_DEV,
                )

        set_default_custom_invoice(self.shop_id, invoice, request.user)
        return self.success_response(data=preference_payload(self.shop_id), message=messages.DEFAULT_INVOICE_UPDATED)
    except Exception:
        return self.server_error(f"Default invoice update failed for shop {self.shop_id}")
