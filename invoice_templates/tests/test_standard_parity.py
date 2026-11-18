"""
Regression guard: the Standard template with default settings must produce
exactly the HTML of the existing invoice generators
(whatsapp_integration/v3/pdf_template.py) for every document type - so a shop
switched to customized invoices sees no change until it customizes.
"""
import base64
import re
from unittest import mock

from django.test import SimpleTestCase

from invoice_templates.v3.context.booking import build_booking_context
from invoice_templates.v3.context.sale import build_sale_context
from invoice_templates.v3.context.tailor import build_tailor_context
from invoice_templates.v3.registry import REGISTRY
from invoice_templates.v3.registry.fonts import font_face_css
from invoice_templates.v3.registry.standard.renderer import _FOOTER_TEXT_PATH
from invoice_templates.v3.services import rendering
from invoice_templates.v3.services.terms import resolve_terms
from whatsapp_integration.v3 import pdf_template as legacy

from .fixtures import (
    make_booking, make_client, make_sale, make_shop, make_tailor_order, numbering_sequence_mock,
)


BOOKING_PRICE_HEADER = '<th style="width: 80px;" class="text-right">PRICE</th>'
# Booking item rows have no other text-right money cell (amount uses amount-cell).
BOOKING_PRICE_CELL = re.compile(r'<td class="text-right">₹[^<]*</td>')
MEASUREMENTS_SECTION = '<div class="measurements-section">'
# Spacer that pushes the T&C block to the page bottom. Its height is measured
# from the content, so it differs now that Standard's summary rows are smaller.
TERMS_SPACER = re.compile(r'(?:<div style="height: \d+px;"></div>)?(?=<div class="footer-content")')
TOTAL_LABEL = re.compile(r'<span class="total-label">([^<]*)</span>')
# Standard prints "Powered by Bookie Buddy" as text in place of the background's outlined footer text.
WATERMARK = re.compile(r'<div class="bb-footer-text">Powered by Bookie Buddy</div>')
# Whether T&C fit on the last content page or start a new one, decided from
# the measured layout; it can differ now that Standard has larger page padding.
TERMS_PAGE_BREAK = re.compile(r'(<div class="footer-content" style=")[^"]*(")')


def _mask_total_labels(html):
    return TOTAL_LABEL.sub('<span class="total-label">LABEL</span>', html)


# Total due sits closer to the "(Inclusive of all taxes.)" note than in the legacy invoice.
NOTE_SPACING = (re.compile(r" has-note"), re.compile(r"(text-align:right;)margin-top:\d+px;"))


def _drop_note_spacing(html):
    html = NOTE_SPACING[0].sub("", html)
    return NOTE_SPACING[1].sub(r"\1", html.replace("text-align:right;margin-top:0;", "text-align:right;"))


SVG_DATA_URI = re.compile(r"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)")


def _without_footer_text(css):
    """Legacy page backgrounds minus the outlined footer text Standard prints as HTML."""
    def strip(match):
        svg = _FOOTER_TEXT_PATH.sub(b"", base64.b64decode(match.group(1)))
        return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("utf-8")
    return SVG_DATA_URI.sub(strip, css)


def _normalize(html):
    html = html.replace("&#x27;", "'")  # same character, attribute-escaped by Django
    html = re.sub(r"\s+", " ", html)
    html = re.sub(r"\s*(<|>)\s*", r"\1", html)
    return html.strip()


class StandardParityTests(SimpleTestCase):
    definition = REGISTRY["standard"]

    def legacy_html(self, document_type, shop, client, obj, items):
        captured = {}

        def capture(html, first_page=True, extra_css="", multi_page=False):
            captured["html"] = html
            captured["extra_css"] = extra_css
            return b"%PDF-fake"

        with mock.patch.object(legacy.BasePDFService, "generate_pdf", staticmethod(capture)), \
                mock.patch("invoice_templates.v3.dispatcher.try_render_customized", return_value=None):
            if document_type == "booking":
                legacy.BookingPDFService.generate_transaction_pdf(shop, client, obj, items, main_service_name="Dress")
            elif document_type == "sale":
                legacy.SalesPDFService.generate_sale_pdf(shop, client, obj, items)
            else:
                legacy.TailorOrderPDFService.generate_tailor_order_pdf(shop, client, obj, items)
        return captured

    def new_parts(self, document_type, shop, client, obj, items):
        terms = resolve_terms(shop, document_type, None)
        if document_type == "booking":
            context = build_booking_context(shop, client, obj, items, main_service_name="Dress", terms=terms)
        elif document_type == "sale":
            context = build_sale_context(shop, client, obj, items, terms=terms)
        else:
            context = build_tailor_context(shop, client, obj, items, terms=terms)
        parts = self.definition.renderer_class(self.definition, dict(self.definition.defaults)).render(context)
        style, spacer = legacy.BasePDFService._resolve_terms_placement(
            parts.main_html, parts.footer_html, font_face_css(parts.font_keys) + "\n" + parts.css,
        )
        html = rendering.DOCUMENT_TEMPLATE.format(
            main_html=parts.main_html, terms_spacer_html=spacer, footer_page_break_style=style,
            footer_html=parts.footer_html, after_html=parts.after_html,
        )
        return parts, html, context

    def assert_terms_layout(self, context):
        """Real PDF: the T&C block sits whole on one page, clear of the 40px bottom padding."""
        document = rendering.render_document(self.definition, dict(self.definition.defaults), context)
        found = []

        def walk(box, page_index):
            element = getattr(box, "element", None)
            if element is not None and "terms" in (element.get("class") or "").split():
                found.append((page_index, box.position_y + box.margin_height()))
            for child in getattr(box, "children", None) or []:
                walk(child, page_index)

        for index, page in enumerate(document.pages):
            walk(page._page_box, index)
        self.assertEqual(len(found), 1, found)  # not split across pages
        page_index, bottom = found[0]
        self.assertGreaterEqual(document.pages[page_index].height - bottom, 40)

    def assert_parity(self, document_type, obj, items, shop=None):
        shop = shop or make_shop()
        client = make_client()
        with mock.patch("shop.models.ShopNumberingSequence.objects", numbering_sequence_mock()):
            old = self.legacy_html(document_type, shop, client, obj, items)
            parts, new_html, context = self.new_parts(document_type, shop, client, obj, items)
        old_html, new_html = _normalize(old["html"]), _normalize(new_html)
        self.assertRegex(new_html, WATERMARK)
        new_html = WATERMARK.sub("", new_html)
        base_css = legacy.BasePDFService.get_base_css(first_page=True, multi_page=True)
        if document_type == "booking":
            # Intentional difference: customized booking invoices add a Price
            # (amount / qty) column after Qty.
            self.assertIn(BOOKING_PRICE_HEADER, new_html)
            new_html = BOOKING_PRICE_CELL.sub("", new_html.replace(BOOKING_PRICE_HEADER, ""))
        if MEASUREMENTS_SECTION in old_html:
            # Intentional difference: the measurements page uses one shared
            # table style (key-value cards and per-person tables); everything
            # before it must match and the base CSS is unchanged.
            self.assertIn(MEASUREMENTS_SECTION, new_html)
            old_html = old_html.split(MEASUREMENTS_SECTION)[0]
            new_html = new_html.split(MEASUREMENTS_SECTION)[0]
        # Intentional difference: the summary rows share one font size, so the
        # measured T&C spacer height changes; everything else must match.
        old_html, new_html = TERMS_SPACER.sub("", old_html), TERMS_SPACER.sub("", new_html)
        # Intentional difference: larger page padding can move the T&C to the
        # next page (or keep it on this one); the real layout is checked below.
        old_html, new_html = TERMS_PAGE_BREAK.sub(r"\1\2", old_html), TERMS_PAGE_BREAK.sub(r"\1\2", new_html)
        self.assert_terms_layout(context)
        # Intentional differences: Standard prints the same summary labels as
        # Classic / Simple (Total Amount Due, Amount Paid, Balance Due, ...) and
        # keeps the total due close to the inclusive-tax note; the rows,
        # values and order are the same (checked against Classic in test_rendering).
        self.assertNotIn('<span class="total-label">BALANCE DUE</span>', new_html)
        old_html, new_html = _mask_total_labels(old_html), _mask_total_labels(new_html)
        old_html, new_html = _drop_note_spacing(old_html), _drop_note_spacing(new_html)
        # CSS: the unchanged base stylesheet first; Standard only appends rules
        # (page padding, same-size summary rows, measurement tables), never edits it.
        self.assertTrue(parts.css.startswith(base_css))
        self.assertEqual(old_html, new_html)
        # Same page backgrounds and fonts.
        # Same backgrounds, minus the outlined "Powered by" text (printed as HTML instead).
        self.assertEqual(_normalize(parts.page_css), _normalize(_without_footer_text(old["extra_css"])))
        self.assertEqual(font_face_css(parts.font_keys), legacy.BasePDFService.get_font_css())

    def test_booking_short(self):
        self.assert_parity("booking", *make_booking(2))

    def test_booking_multi_page_with_measurements(self):
        self.assert_parity("booking", *make_booking(14))

    def test_booking_cancelled_inclusive_tax(self):
        self.assert_parity("booking", *make_booking(2, cancelled=True, inclusive=True))

    def test_sale_short(self):
        self.assert_parity("sale", *make_sale(2))

    def test_sale_multi_page(self):
        self.assert_parity("sale", *make_sale(16))

    def test_tailor_order(self):
        self.assert_parity("tailor_order", *make_tailor_order(2))

    def test_tailor_order_cancelled(self):
        self.assert_parity("tailor_order", *make_tailor_order(3, cancelled=True))

    def test_builtin_terms_when_shop_has_none(self):
        self.assert_parity("sale", *make_sale(2), shop=make_shop(terms_and_conditions=None))
