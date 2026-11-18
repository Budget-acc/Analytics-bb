from drf_spectacular.utils import OpenApiResponse, extend_schema

from invoice_templates.models import ShopCustomInvoice
from invoice_templates.v3.registry import REGISTRY
from invoice_templates.v3.services.custom_invoices import usage

from ...utils import docs, messages
from .helpers import ITEM_EXAMPLE, LIST_ITEM_SHAPE, USAGE_SHAPE, default_invoice_id, invoice_summary


@extend_schema(
    tags=[docs.TAG],
    summary="List the shop's customized invoices",
    description=(
        "Returns the active shop's customized invoices and how many more it can create.\n\n"
        "Purpose: Drives the 'My invoices' screen - each customized invoice has the shop's own "
        "name, the template it is built from and whether it is the default.\n\n"
        "Context: A shop can create at most 4 customized invoices (usage.limit); updating "
        "existing ones is unlimited and deleting one frees a slot. Disable the Create button "
        "when usage.remaining is 0. Newest first.\n\n"
        "Accepted inputs: no path, query or body parameters.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=(
            f"Success. Exact message: {messages.CUSTOM_INVOICES_FETCHED}\n\n"
            "Response shape (data):\n" + USAGE_SHAPE + "\n"
            "- custom_invoices (array), each item:\n" + LIST_ITEM_SHAPE
        )),
        403: docs.R403,
        500: docs.R500,
    },
    examples=[docs.envelope_example("Success", messages.CUSTOM_INVOICES_FETCHED, data={
        "usage": {"used": 1, "limit": 4, "remaining": 3},
        "custom_invoices": [ITEM_EXAMPLE],
    })],
)
def list(self, request):
    try:
        # Not list(...): this module's view function is itself named `list`.
        rows = [
            *ShopCustomInvoice.objects.filter(shop_id=self.shop_id).select_related("template")
            .order_by("-created_at", "-id")
        ]
        # Every row counts toward the limit; rows whose template was removed
        # from the code registry are not listed (they cannot be edited/rendered).
        invoices = [invoice for invoice in rows if invoice.template.code in REGISTRY]
        default_id = default_invoice_id(self.shop_id)
        return self.success_response(
            data={
                "usage": usage(self.shop_id, used=len(rows)),
                "custom_invoices": [invoice_summary(invoice, default_id) for invoice in invoices],
            },
            message=messages.CUSTOM_INVOICES_FETCHED,
        )
    except Exception:
        return self.server_error(f"Customized invoice list failed for shop {self.shop_id}")
