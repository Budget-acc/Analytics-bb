from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action

from invoice_templates.v3.services.sharing import generate_share, share_payload

from ...utils import docs, messages

SHARE_SHAPE = (
    "Response shape (data):\n"
    "- share_code (string): normalized 12-character code (store/compare this).\n"
    "- display_code (string): the same code grouped for reading, e.g. K7QM-2XRP-9HTD.\n"
    "- qr_payload (string): text to encode in the QR code "
    "(bookiebuddy://invoice-template/import?code=<share_code>).\n"
    "- custom_invoice_id (integer), invoice_name (string): the shared customized invoice and the "
    "name captured in the code.\n"
    "- template_code (string): template the code belongs to.\n"
    "- created_at (string, \"YYYY-MM-DD HH:MM:SS\", IST), expires_at (same format): 24 hours after "
    "created_at - after it the code can no longer be looked up or imported and generate issues a new one.\n"
    "- import_count (integer): how many times other shops imported it.\n"
    "- is_outdated (boolean): the customized invoice was changed after this code was created; "
    "generate again to share the latest version."
)

SHARE_EXAMPLE = {
    "share_code": "K7QM2XRP9HTD",
    "display_code": "K7QM-2XRP-9HTD",
    "qr_payload": "bookiebuddy://invoice-template/import?code=K7QM2XRP9HTD",
    "custom_invoice_id": 42,
    "invoice_name": "Wedding Season",
    "template_code": "classic",
    "created_at": "2026-10-16 10:20:01",
    "expires_at": "2026-10-17 10:20:01",
    "import_count": 0,
    "is_outdated": False,
}


@extend_schema(
    tags=[docs.TAG],
    summary="Generate a share code for a customized invoice",
    description=(
        "Creates a share code (and QR payload) for one of the active shop's customized invoices, "
        "or returns the current code if it has not expired and the invoice did not change since it "
        "was made.\n\n"
        "Purpose: Lets an owner reuse a customized invoice in another shop or branch - the other "
        "shop enters the code or scans the QR and imports it as its own new customized invoice.\n\n"
        "Context: The name and settings are copied into the code at generation time; later changes "
        "are not included until a new code is generated (is_outdated tells when). A code is valid "
        "for 24 hours (expires_at): while it is valid and the invoice is unchanged the same code is "
        "returned (200); once it has expired or the invoice changed, the previous code is revoked "
        "and a new one valid for 24 hours is returned (201). Only the name and style "
        "settings are shared - never the logo, shop details, terms or invoice data. Codes are "
        "unique across all shops. Deleting the customized invoice invalidates its code.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- custom_invoice_id: customized invoice id.\n\n"
        + docs.AUTH_NOTE
    ),
    parameters=[docs.SHARE_CUSTOM_INVOICE_ID_PATH, docs.SHOP_HEADER],
    request=None,
    responses={
        200: OpenApiResponse(description=f"Existing code returned. Exact message: {messages.SHARE_EXISTING}\n\n" + SHARE_SHAPE),
        201: OpenApiResponse(description=f"New code created. Exact message: {messages.SHARE_CREATED}\n\n" + SHARE_SHAPE),
        403: docs.R403,
        404: docs.R404_CUSTOM_INVOICE,
        500: docs.R500,
    },
    examples=[
        docs.envelope_example("Created", messages.SHARE_CREATED, data=SHARE_EXAMPLE, status_codes=["201"]),
    ],
)
@action(detail=True, methods=["post"], url_path="generate")
def generate(self, request, custom_invoice_id=None):
    try:
        invoice = self.get_invoice(custom_invoice_id)
        if invoice is None:
            return self.invoice_not_found()
        share, created = generate_share(invoice, request.user)
        return self.success_response(
            data=share_payload(share, invoice),
            message=messages.SHARE_CREATED if created else messages.SHARE_EXISTING,
            http_status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    except Exception:
        return self.server_error(f"Invoice share generation failed for shop {self.shop_id}")
