from invoice_templates.v3.services.sharing import ShareError

from ...utils import messages
from ..mixins import InvoiceTemplateBaseViewSet


class TemplateShareViewSet(InvoiceTemplateBaseViewSet):
    """
    Share codes for customized invoices: generate / read / revoke the code of
    one of the active shop's customized invoices, look up a code received
    from another shop, and import it as a new customized invoice. Detail
    routes are addressed by customized invoice id.
    """
    lookup_field = "custom_invoice_id"
    lookup_value_regex = r"\d+"

    def share_error_response(self, error):
        """400 for a share code that cannot be looked up / imported (lookup and import share it)."""
        if error.reason == ShareError.EXPIRED:
            return self.error_response(
                message=messages.SHARE_CODE_EXPIRED,
                dev_message=messages.SHARE_CODE_EXPIRED_DEV,
                error_code=messages.SHARE_CODE_EXPIRED_ERROR_CODE,
            )
        if error.reason == ShareError.OWN_CODE:
            return self.error_response(message=messages.OWN_SHARE_CODE, dev_message=messages.OWN_SHARE_CODE_DEV)
        return self.error_response(message=messages.INVALID_SHARE_CODE, dev_message=messages.INVALID_SHARE_CODE_DEV)
