from drf_spectacular.utils import OpenApiResponse, extend_schema

from invoice_templates.v3.services.custom_invoices import delete_custom_invoice, usage

from ...utils import docs, messages
from .helpers import USAGE_SHAPE


@extend_schema(
    tags=[docs.TAG],
    summary="Delete a customized invoice",
    description=(
        "Deletes one of the active shop's customized invoices.\n\n"
        "Purpose: Remove an invoice the shop no longer needs and free a slot for a new one.\n\n"
        "Context: If it was the default, the shop's real invoices go back to the standard "
        "invoice with default settings (same look as before customization). Its share codes stop "
        "working; shops that already imported it keep their own copy. Other customized invoices "
        "and other shops are not affected.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- id: customized invoice id.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.CUSTOM_INVOICE_ID_PATH, docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact message: {messages.CUSTOM_INVOICE_DELETED}\n\n"
            "Response shape (data):\n- was_default (boolean): it was the default invoice.\n" + USAGE_SHAPE
        )),
        403: docs.R403,
        404: docs.R404_CUSTOM_INVOICE,
        500: docs.R500,
    },
    examples=[docs.envelope_example("Success", messages.CUSTOM_INVOICE_DELETED, data={
        "was_default": False, "usage": {"used": 3, "limit": 4, "remaining": 1},
    })],
)
def destroy(self, request, pk=None):
    try:
        invoice = self.get_invoice(pk)
        if invoice is None:
            return self.invoice_not_found()
        was_default = invoice.default_for_preferences.exists()
        delete_custom_invoice(invoice)
        return self.success_response(
            data={"was_default": was_default, "usage": usage(self.shop_id)},
            message=messages.CUSTOM_INVOICE_DELETED,
        )
    except Exception:
        return self.server_error(f"Customized invoice delete failed for shop {self.shop_id}, id {pk}")
