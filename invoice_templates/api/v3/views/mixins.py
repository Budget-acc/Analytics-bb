import logging
import re

from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.http import HttpResponse

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from invoice_templates.models import ShopInvoicePreference
from invoice_templates.v3.registry import get_definition
from invoice_templates.v3.services.configuration import (
    ConfigurationError, effective_configuration, get_active_template, validate_configuration,
)
from invoice_templates.v3.services.custom_invoices import (
    InvalidName, LimitReached, NameTaken, get_shop_invoice, usage,
)
from invoice_templates.v3.services.rendering import render_preview
from shop.api.v3.utils.shop import get_active_shop_id
from shop.models import Shop
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission
from subscriptions.api.admin_v3.utils.role_permissions import OwnerOnlyPermission

from ..utils import messages

logger = logging.getLogger("api_requests")

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:"*?<>|]')


def preview_filename(label, document_type) -> str:
    """
    'Wedding Season' + 'tailor_order' -> 'Invoice_Preview_Wedding_Season_Tailor_Order.pdf'.
    Same sanitizing rules as BasePDFService.build_invoice_filename (ASCII only,
    no filename/header-breaking characters), so a shop-chosen name is safe in
    the Content-Disposition header.
    """
    parts = []
    for raw in (label, str(document_type).replace("_", " ").title()):
        text = str(raw or "").encode("ascii", "ignore").decode("ascii")
        text = _UNSAFE_FILENAME_CHARS.sub("", text)
        text = re.sub(r"\s+", "_", text).strip("_")
        if text:
            parts.append(text)
    return "_".join(["Invoice_Preview", *parts]) + ".pdf"


THROTTLE_MESSAGES = {
    "preview": messages.PREVIEW_THROTTLED,
    "preview_custom_invoice": messages.PREVIEW_THROTTLED,
    "lookup": messages.SHARE_THROTTLED,
    "import_shared": messages.SHARE_THROTTLED,
}


class InvoiceTemplateBaseViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Common base for the invoice template ViewSets: owner-only, active
    subscription, and every request scoped to the active shop resolved from
    the authenticated user (X-Active-Shop-ID) - a client-supplied shop id is
    never trusted.
    """
    permission_classes = [IsAuthenticated, OwnerOnlyPermission, SubscriptionActivePermission]
    authentication_classes = [CutoffJWTAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.warning(f"Invoice template API shop access denied: {e}")
            raise PermissionDenied({
                "status": "permission_denied",
                "message": messages.SHOP_ACCESS_DENIED,
                "data": None,
            })

    def throttled(self, request, wait):
        raise Throttled(detail={
            "status": "rate_limited",
            "message": THROTTLE_MESSAGES.get(getattr(self, "action", None), messages.SHARE_THROTTLED),
            "data": {"retry_after_seconds": int(wait) if wait else None},
            "dev_message": "Rate limit exceeded for this endpoint; retry after data.retry_after_seconds.",
        })

    # ---- helpers ---------------------------------------------------------
    def resolve_template(self, code):
        """(InvoiceTemplate, TemplateDefinition) for an active code, or (None, None)."""
        template = get_active_template(code)
        if template is None:
            return None, None
        return template, get_definition(template.code)

    def template_not_found(self):
        return self.not_found_response(
            message=messages.TEMPLATE_NOT_FOUND, dev_message=messages.TEMPLATE_NOT_FOUND_DEV,
        )

    def field_errors_response(self, message, errors, dev_message):
        """
        400 in the standard envelope, keeping per-field errors in
        data.validation_errors (StandardResponse.validation_error drops data
        for non-success statuses, so the response is built here).
        """
        logger.info(f"Invoice template API validation failed: {message} {list(errors)[:10]}")
        return Response(
            {
                "status": "validation_error",
                "message": message,
                "data": {"validation_errors": errors},
                "dev_message": dev_message,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def get_invoice(self, invoice_id):
        """The active shop's customized invoice by id, or None (another shop's id -> None)."""
        return get_shop_invoice(self.shop_id, invoice_id)

    def invoice_not_found(self):
        return self.not_found_response(
            message=messages.CUSTOM_INVOICE_NOT_FOUND, dev_message=messages.CUSTOM_INVOICE_NOT_FOUND_DEV,
        )

    def custom_invoice_error_response(self, error):
        """400 for the creation-limit / name errors raised by the service layer."""
        if isinstance(error, LimitReached):
            return self.error_with_data(
                messages.limit_reached_message(error.limit),
                messages.LIMIT_REACHED_DEV,
                {"usage": usage(self.shop_id)},
            )
        if isinstance(error, NameTaken):
            return self.error_response(message=messages.NAME_TAKEN, dev_message=messages.NAME_TAKEN_DEV)
        if isinstance(error, InvalidName):
            return self.error_response(message=messages.INVALID_NAME, dev_message=messages.INVALID_NAME_DEV)
        raise error

    def error_with_data(self, message, dev_message, data, http_status=status.HTTP_400_BAD_REQUEST):
        """Error in the standard envelope that keeps `data` (StandardResponse.error drops it)."""
        return Response(
            {"status": "error", "message": message, "data": data, "dev_message": dev_message},
            status=http_status,
        )

    def preview_configuration(self, definition, raw, base=None):
        """
        (configuration, error_response) for a preview request: `raw` (unsaved
        values, strictly validated) overlaid on `base` (saved values) and the
        template defaults. Nothing is persisted.
        """
        if raw is None:
            return effective_configuration(definition, base), None
        if not isinstance(raw, dict):
            return None, self.error_response(
                message=messages.CONFIGURATION_REQUIRED, dev_message=messages.CONFIGURATION_REQUIRED_DEV,
            )
        try:
            return validate_configuration(definition, raw, base=base), None
        except ConfigurationError as e:
            return None, self.field_errors_response(messages.INVALID_SETTINGS, e.errors, messages.INVALID_SETTINGS_DEV)

    def preview_pdf_response(self, definition, configuration, document_type, label):
        """
        Render the one-page preview (shop header + resolved terms + sample data)
        and return it as a downloadable PDF file - same response format as the
        existing invoice download APIs (booking/sale send_invoice):
        HttpResponse(application/pdf) + Content-Disposition: attachment.
        """
        shop = Shop.objects.get(id=self.shop_id)
        preference = ShopInvoicePreference.objects.filter(shop_id=self.shop_id).first()
        try:
            pdf_bytes = render_preview(definition, configuration, document_type, shop, preference)
        except Exception:
            logger.exception(f"Invoice preview render failed: shop {self.shop_id}, template {definition.code}")
            return self.error_response(
                message=messages.PREVIEW_FAILED,
                dev_message=messages.PREVIEW_FAILED_DEV,
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{preview_filename(label, document_type)}"'
            response["Content-Length"] = str(len(pdf_bytes))
            response["Cache-Control"] = "no-store"
            return response
        except Exception:
            logger.exception(f"Invoice preview response failed: shop {self.shop_id}, template {definition.code}")
            return self.error_response(
                message=messages.PREVIEW_RESPONSE_FAILED,
                dev_message=messages.PREVIEW_RESPONSE_FAILED_DEV,
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def server_error(self, log_message):
        logger.exception(log_message)
        return self.error_response(
            message=messages.SERVER_ERROR,
            dev_message=messages.SERVER_ERROR_DEV,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
