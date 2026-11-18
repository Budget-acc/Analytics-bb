"""
Invoice rendering pipeline shared by every template and document type.

    InvoiceContext + TemplateDefinition + configuration
        -> renderer.render()            (HTML parts + CSS)
        -> BasePDFService._resolve_terms_placement  (existing, measured T&C placement)
        -> WeasyPrint                   (same document structure as the existing invoices)
"""
import base64
import logging
logger = logging.getLogger("api_requests")

from urllib.parse import urlparse


try:
    from weasyprint import CSS, HTML, default_url_fetcher
    from weasyprint.text.fonts import FontConfiguration

    WEASYPRINT_AVAILABLE = True

except (ImportError, OSError) as e:
    CSS = None
    HTML = None
    FontConfiguration = None
    default_url_fetcher = None

    WEASYPRINT_AVAILABLE = False

    logger = logging.getLogger("api_requests")
    logger.warning(
        "WeasyPrint unavailable. PDF generation disabled: %s", e
    )

from whatsapp_integration.v3.pdf_template import BasePDFService, _silence_pdf_generation_logs

from invoice_templates.models import DocumentType
from invoice_templates.v3.context.booking import build_booking_context
from invoice_templates.v3.context.sale import build_sale_context
from invoice_templates.v3.context.sample import build_sample_context
from invoice_templates.v3.context.tailor import build_tailor_context
from invoice_templates.v3.registry.fonts import font_face_css

from .configuration import resolve_for_shop
from .terms import resolve_terms


DOCUMENT_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body>
    <div class="content">
        <div class="main-content">
            {main_html}
        </div>

        {terms_spacer_html}
        <div class="footer-content" style="{footer_page_break_style}">
            {footer_html}
        </div>
        {after_html}
    </div>
</body>
</html>"""


def _safe_url_fetcher(url, *args, **kwargs):
    """
    Only inline data: URIs (logos, item images, backgrounds) and local font
    files are loaded. Anything else is refused, so content can never make
    the server fetch a network URL while rendering.
    """
    scheme = urlparse(url).scheme.lower()
    if scheme in ("data", "file"):
        return default_url_fetcher(url, *args, **kwargs)
    raise ValueError(f"Blocked external resource in invoice PDF: {url[:80]}")


def render_pdf_bytes(definition, configuration, context, single_page=False) -> bytes:
    with _silence_pdf_generation_logs():
        return render_document(definition, configuration, context, single_page=single_page).write_pdf()


def render_document(definition, configuration, context, single_page=False):
    """Laid-out WeasyPrint document (pages available via .pages)."""
    renderer = definition.renderer_class(definition, configuration)
    parts = renderer.render(context)

    measure_css = font_face_css(parts.font_keys) + "\n" + parts.css
    footer_page_break_style, terms_spacer_html = BasePDFService._resolve_terms_placement(
        parts.main_html, parts.footer_html, measure_css
    )
    document_html = DOCUMENT_TEMPLATE.format(
        main_html=parts.main_html,
        terms_spacer_html=terms_spacer_html,
        footer_page_break_style=footer_page_break_style,
        footer_html=parts.footer_html,
        after_html=parts.after_html,
    )

    font_config = FontConfiguration()
    stylesheet = CSS(
        string=measure_css + "\n" + parts.page_css,
        font_config=font_config,
        url_fetcher=_safe_url_fetcher,
    )
    with _silence_pdf_generation_logs():
        document = HTML(string=document_html, url_fetcher=_safe_url_fetcher).render(
            stylesheets=[stylesheet], font_config=font_config,
        )
        if single_page and len(document.pages) > 1:
            document = document.copy(document.pages[:1])
    return document


def render_invoice_base64(document_type, *, shop, client, obj, items, main_service_name="") -> str:
    """
    Real invoice with the shop's default template + saved settings.
    Same return contract as the existing generators (base64 PDF string).
    """
    resolved = resolve_for_shop(shop.id)
    terms = resolve_terms(shop, document_type, resolved.preference)

    if document_type == DocumentType.BOOKING:
        context = build_booking_context(shop, client, obj, items, main_service_name=main_service_name, terms=terms)
    elif document_type == DocumentType.SALE:
        context = build_sale_context(shop, client, obj, items, terms=terms)
    elif document_type == DocumentType.TAILOR_ORDER:
        context = build_tailor_context(shop, client, obj, items, terms=terms)
    else:
        raise ValueError(f"Unsupported invoice document type: {document_type}")

    pdf_bytes = render_pdf_bytes(resolved.definition, resolved.configuration, context)
    logger.info(
        f"Customized invoice rendered: shop={shop.id} type={document_type} "
        f"template={resolved.definition.code} size={len(pdf_bytes)}B"
    )
    return base64.b64encode(pdf_bytes).decode("utf-8")


def render_preview(definition, configuration, document_type, shop, preference=None) -> bytes:
    """
    One-page preview with static sample data and the shop's real header and
    resolved terms. Never touches saved settings.
    """
    return render_pdf_bytes(
        definition, configuration, preview_context(document_type, shop, preference), single_page=True,
    )


def preview_context(document_type, shop, preference=None):
    """Sample invoice data (built by the real context builders) with the shop's real header and resolved terms."""
    terms = resolve_terms(shop, document_type, preference)
    return build_sample_context(document_type, shop, terms)
