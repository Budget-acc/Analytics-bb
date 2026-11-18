from drf_spectacular.utils import OpenApiResponse, extend_schema

from invoice_templates.v3.services.sharing import get_active_share, share_payload

from ...utils import docs, messages
from .generate import SHARE_EXAMPLE, SHARE_SHAPE


@extend_schema(
    tags=[docs.TAG],
    summary="Get the current share code of a customized invoice",
    description=(
        "Returns the active (not revoked, not expired) share code of one of the active shop's "
        "customized invoices, or null when there is none.\n\n"
        "Purpose: Shows the existing code/QR on the share screen without creating a new one.\n\n"
        "Context: Read-only. Use POST share/{custom_invoice_id}/generate/ to create one.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- custom_invoice_id: customized invoice id.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHARE_CUSTOM_INVOICE_ID_PATH, docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact messages: {messages.SHARE_FETCHED} (data = share, shape below) or "
            f"{messages.NO_ACTIVE_SHARE_DATA} (data = null).\n\n" + SHARE_SHAPE
        )),
        403: docs.R403,
        404: docs.R404_CUSTOM_INVOICE,
        500: docs.R500,
    },
    examples=[docs.envelope_example("Success", messages.SHARE_FETCHED, data=SHARE_EXAMPLE)],
)
def retrieve_active(self, request, custom_invoice_id=None):
    try:
        invoice = self.get_invoice(custom_invoice_id)
        if invoice is None:
            return self.invoice_not_found()
        share = get_active_share(invoice)
        if share is None:
            return self.success_response(data=None, message=messages.NO_ACTIVE_SHARE_DATA)
        return self.success_response(data=share_payload(share, invoice), message=messages.SHARE_FETCHED)
    except Exception:
        return self.server_error(f"Invoice share fetch failed for shop {self.shop_id}")
