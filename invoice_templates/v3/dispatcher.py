"""
Entry point called from the existing invoice generators.

    result = try_render_customized("booking", shop=..., client=..., obj=..., items=...)
    if result is not None:
        return result
    # ...existing implementation continues unchanged...

Returns None whenever the existing implementation should run:
  - the switch is off (globally or for this shop), or
  - the customized renderer failed and INVOICE_CUSTOMIZATION_FALLBACK_ON_ERROR
    is on (the error is logged and the shop still gets its invoice).
"""
import logging

from django.conf import settings

from .switch import is_customization_enabled

logger = logging.getLogger("api_requests")


def try_render_customized(document_type, *, shop, client, obj, items, main_service_name=""):
    try:
        if not is_customization_enabled(shop):
            return None
    except Exception:
        # The switch itself must never break invoice generation.
        logger.exception("Invoice customization switch check failed; using the existing invoice.")
        return None

    try:
        from .services.rendering import render_invoice_base64

        return render_invoice_base64(
            document_type, shop=shop, client=client, obj=obj,
            items=items, main_service_name=main_service_name,
        )
    except Exception:
        if getattr(settings, "INVOICE_CUSTOMIZATION_FALLBACK_ON_ERROR", True):
            logger.exception(
                f"Customized {document_type} invoice failed for shop {getattr(shop, 'id', None)}; "
                f"falling back to the existing invoice."
            )
            return None
        raise
