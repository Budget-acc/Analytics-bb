from drf_spectacular.utils import OpenApiResponse, extend_schema

from invoice_templates.v3.services.sharing import ShareError, revoke_share

from ...utils import docs, messages


@extend_schema(
    tags=[docs.TAG],
    summary="Revoke the share code of a customized invoice",
    description=(
        "Revokes the current share code of one of the active shop's customized invoices so it can "
        "no longer be looked up or imported.\n\n"
        "Purpose: Stop sharing, or invalidate a code that was shared too widely.\n\n"
        "Context: Shops that already imported it keep their own copy. To share again, call "
        "POST share/{custom_invoice_id}/generate/ - a new, different code is issued.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- custom_invoice_id: customized invoice id.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHARE_CUSTOM_INVOICE_ID_PATH, docs.SHOP_HEADER],
    responses={
        200: OpenApiResponse(description=f"Success. Exact message: {messages.SHARE_REVOKED} (data = {{}})"),
        403: docs.R403,
        404: OpenApiResponse(description=(
            "Not found. Exact messages:\n" + messages.bullet(messages.CUSTOM_INVOICE_NOT_FOUND, messages.NO_ACTIVE_SHARE)
        )),
        500: docs.R500,
    },
)
def revoke(self, request, custom_invoice_id=None):
    try:
        invoice = self.get_invoice(custom_invoice_id)
        if invoice is None:
            return self.invoice_not_found()
        try:
            revoke_share(invoice)
        except ShareError:
            return self.not_found_response(message=messages.NO_ACTIVE_SHARE, dev_message=messages.NO_ACTIVE_SHARE_DEV)
        return self.success_response(data={}, message=messages.SHARE_REVOKED)
    except Exception:
        return self.server_error(f"Invoice share revoke failed for shop {self.shop_id}")
