from drf_spectacular.utils import OpenApiResponse, extend_schema

from ...utils import docs, messages
from .helpers import DETAIL_EXAMPLE, DETAIL_SHAPE, default_invoice_id, invoice_detail


@extend_schema(
    tags=[docs.TAG],
    summary="Get a customized invoice",
    description=(
        "Returns one of the active shop's customized invoices with its settings, the template "
        "defaults and the font list.\n\n"
        "Purpose: Opens a customized invoice for editing - fill the five color pickers and the "
        "font selector from `configuration`.\n\n"
        "Context: Only the active shop's own customized invoices are returned; any other id is "
        "'not found'.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- id: customized invoice id.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.CUSTOM_INVOICE_ID_PATH, docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact message: {messages.CUSTOM_INVOICE_FETCHED}\n\nResponse shape (data):\n" + DETAIL_SHAPE
        )),
        403: docs.R403,
        404: docs.R404_CUSTOM_INVOICE,
        500: docs.R500,
    },
    examples=[docs.envelope_example("Success", messages.CUSTOM_INVOICE_FETCHED, data=DETAIL_EXAMPLE)],
)
def retrieve(self, request, pk=None):
    try:
        invoice = self.get_invoice(pk)
        if invoice is None:
            return self.invoice_not_found()
        return self.success_response(
            data=invoice_detail(invoice, default_invoice_id(self.shop_id)),
            message=messages.CUSTOM_INVOICE_FETCHED,
        )
    except Exception:
        return self.server_error(f"Customized invoice retrieve failed for shop {self.shop_id}, id {pk}")
