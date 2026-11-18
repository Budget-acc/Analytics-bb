import re
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from invoice_templates.v3.context.booking import _unit_price, build_booking_context
from invoice_templates.v3.context.common import parse_info_lines, tax_rows
from invoice_templates.v3.context.sale import build_sale_context
from invoice_templates.v3.context.sample import build_sample_context
from invoice_templates.v3.context.tailor import build_tailor_context
from invoice_templates.v3.registry import REGISTRY
from invoice_templates.v3.services.configuration import validate_configuration
from invoice_templates.v3.services.rendering import (
    _safe_url_fetcher, preview_context, render_document, render_preview,
)
from whatsapp_integration.v3.pdf_template import BasePDFService

from .fixtures import make_booking, make_client, make_sale, make_shop, make_tailor_order, numbering_sequence_mock

DOCUMENT_TYPES = ("booking", "sale", "tailor_order")
# The booking builder (also used for the preview sample) reads the shop's product-code prefix.
NUMBERING = "shop.models.ShopNumberingSequence.objects"


def sample_context(document_type, terms, shop=None):
    """Preview sample context for a test shop (no database)."""
    with mock.patch(NUMBERING, numbering_sequence_mock()):
        return build_sample_context(document_type, shop or make_shop(), terms)


def real_contexts():
    """Real-builder contexts covering every summary variant (GST, inclusive note, cancelled)."""
    shop, client = make_shop(), make_client()
    sale, sale_items = make_sale(2)
    sale.is_tax_applied, sale.tax_name, sale.tax_rate = True, "GST", Decimal("5")
    sale.tax_amount, sale.tax_calculation_type = Decimal("200"), "exclusive"
    with mock.patch(NUMBERING, numbering_sequence_mock()):
        return {
            "booking": build_booking_context(shop, client, *make_booking(2), terms=["T1"]),
            "booking_inclusive": build_booking_context(shop, client, *make_booking(2, inclusive=True), terms=["T1"]),
            "booking_cancelled": build_booking_context(shop, client, *make_booking(2, cancelled=True), terms=["T1"]),
            "booking_room": build_booking_context(
                shop, client, *make_booking(2), main_service_name="Room Booking", terms=["T1"],
            ),
            "sale_gst": build_sale_context(shop, client, sale, sale_items, terms=["T1"]),
            "tailor": build_tailor_context(shop, client, *make_tailor_order(2), terms=["T1"]),
            "tailor_cancelled": build_tailor_context(shop, client, *make_tailor_order(2, cancelled=True), terms=["T1"]),
        }


def text_boxes(document, container_class):
    """[(text, font_size_pt, top, bottom)] of the text inside the first `container_class` element."""
    found = []

    def walk(box, inside):
        element = getattr(box, "element", None)
        inside = inside or (element is not None and container_class in (element.get("class") or "").split())
        if inside and getattr(box, "text", "").strip():
            found.append((box.text.strip(), round(box.style["font_size"] * 0.75, 1),
                          box.position_y, box.position_y + box.height))
        for child in getattr(box, "children", None) or []:
            walk(child, inside)

    for page in document.pages:
        walk(page._page_box, False)
        if found:
            break
    return found


SUMMARY_CONTAINER = {"classic": "c-totals", "standard": "totals", "simple": "s-totals"}


class SummaryConsistencyTests(SimpleTestCase):
    """Real invoices: summary sizes per template, note spacing, Classic booking headings."""

    def test_total_paid_balance_sizes_same_for_every_document_type(self):
        contexts = real_contexts()
        for code, definition in REGISTRY.items():
            sizes = {}
            for name, context in contexts.items():
                boxes = text_boxes(render_document(definition, definition.defaults, context), SUMMARY_CONTAINER[code])
                for text, size, _, _ in boxes:
                    for field in ("Total Amount Due", "Amount Paid", "Balance Due"):
                        if text == field:
                            sizes.setdefault(field, set()).add(size)
            with self.subTest(code=code):
                self.assertEqual(set(sizes), {"Total Amount Due", "Amount Paid", "Balance Due"})
                for field, values in sizes.items():
                    self.assertEqual(len(values), 1, f"{code} {field}: {values}")

    def test_total_due_close_to_inclusive_tax_note(self):
        context = real_contexts()["booking_inclusive"]
        for code, definition in REGISTRY.items():
            with self.subTest(code=code):
                boxes = text_boxes(render_document(definition, definition.defaults, context), SUMMARY_CONTAINER[code])
                total_bottom = next(bottom for text, _, _, bottom in boxes if text == "Total Amount Due")
                note_top = next(top for text, _, top, _ in boxes if text.startswith("(Inclusive"))
                self.assertLessEqual(note_top - total_bottom, 4)

    def test_classic_booking_headings_use_text_color(self):
        definition = REGISTRY["classic"]
        config = configured(definition, colors={"primary": "#112233", "text": "#445566"})
        contexts = real_contexts()
        renderer = definition.renderer_class(definition, config)
        parts = renderer.render(contexts["booking"])
        self.assertIn(".c-section-heading.text-colored { color: #445566; }", parts.css)
        for heading in ("Customer", "Booking Details"):
            self.assertIn(f'<div class="c-section-heading text-colored">{heading}</div>', parts.main_html)
        self.assertIn('<div class="c-section-heading text-colored">Terms and Conditions</div>', parts.footer_html)
        self.assertNotIn("text-colored", parts.after_html)  # measurement headings stay primary
        for name in ("sale_gst", "tailor"):
            other = renderer.render(contexts[name])
            self.assertNotIn("text-colored", other.main_html + other.footer_html, name)
        simple = REGISTRY["simple"]
        simple_parts = simple.renderer_class(simple, simple.defaults).render(contexts["booking"])
        self.assertNotIn("text-colored", simple_parts.main_html + simple_parts.footer_html)


@mock.patch(NUMBERING, numbering_sequence_mock())
class PreviewTests(SimpleTestCase):
    def test_preview_is_one_page_for_every_template_and_document_type(self):
        # Long terms would push a real invoice onto a second page.
        long_terms = [f"Term number {i} with enough words to wrap across the line." for i in range(25)]
        shop = make_shop(terms_and_conditions=long_terms)
        for code, definition in REGISTRY.items():
            for document_type in DOCUMENT_TYPES:
                with self.subTest(code=code, document_type=document_type):
                    document = render_document(
                        definition, definition.defaults, preview_context(document_type, shop), single_page=True,
                    )
                    self.assertEqual(len(document.pages), 1)
        # And the API path returns real PDF bytes.
        pdf = render_preview(REGISTRY["classic"], REGISTRY["classic"].defaults, "sale", shop)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_preview_sample_shows_terms_on_the_page(self):
        definition = REGISTRY["standard"]
        context = sample_context("booking", ["Short term."])
        parts = definition.renderer_class(definition, definition.defaults).render(context)
        style, _ = BasePDFService._resolve_terms_placement(parts.main_html, parts.footer_html, parts.css)
        self.assertNotIn("page-break-before", style)  # terms fit on the preview page

    def test_preview_shows_cancelled_and_gst_exactly_like_a_real_invoice(self):
        # The sample goes through the real builders: booking and tailor are
        # cancelled (sales have no cancelled state), and every type has the
        # exclusive-GST CGST + SGST rows from tax_rows().
        expected = {
            "booking": ("BOOKING CANCELLED", tax_rows("GST", 5, Decimal("152.50"), True, False)),
            "sale": (None, tax_rows("GST", 5, Decimal("310"), True, False)),
            "tailor_order": ("ORDER CANCELLED", tax_rows("GST", 5, Decimal("500"), True, False)),
        }
        shop = make_shop()
        for document_type, (cancelled_label, gst_rows) in expected.items():
            with self.subTest(document_type=document_type):
                context = preview_context(document_type, shop)
                labels = [(row.label, row.value) for row in context.totals]
                for row in gst_rows:
                    self.assertIn((row.label, row.value), labels)
                self.assertEqual(context.is_cancelled, cancelled_label is not None)
                if cancelled_label:
                    self.assertIn(cancelled_label, [row.label for row in context.totals])
                    self.assertNotIn("BALANCE DUE", [row.label for row in context.totals])  # as a real cancelled PDF
                for code, definition in REGISTRY.items():
                    html = definition.renderer_class(definition, definition.defaults).render(context).main_html
                    self.assertIn("CGST (2.5%)", html, code)
                    self.assertIn("SGST (2.5%)", html, code)
                    if cancelled_label:
                        # Classic/Standard: totals row; Simple: CANCELLED below the shop details.
                        self.assertRegex(html, r"(?i)(booking|order) cancelled|>CANCELLED<", code)

    def test_preview_matches_real_cancelled_invoice_rendering(self):
        # Same markup for the cancelled row as an invoice built from real-shaped records.
        booking, items = make_booking(2, cancelled=True)
        real = build_booking_context(make_shop(), make_client(), booking, items, terms=["T1"])
        preview = preview_context("booking", make_shop())
        for code, definition in REGISTRY.items():
            renderer = definition.renderer_class(definition, definition.defaults)
            real_rows = [e for e in renderer.totals(real) if e["row"].kind == "cancelled"]
            preview_rows = [e for e in renderer.totals(preview) if e["row"].kind == "cancelled"]
            self.assertEqual(
                [(e["label"], e["css"]) for e in real_rows], [(e["label"], e["css"]) for e in preview_rows], code,
            )


def configured(definition, colors=None, font=None):
    """Complete configuration: template defaults + the given overrides."""
    raw = {}
    if colors:
        raw["colors"] = colors
    if font:
        raw["font"] = font
    return validate_configuration(definition, raw)


LOGO = "data:image/png;base64,iVBORw0KGgo="


class StandardCustomizationTests(SimpleTestCase):
    definition = REGISTRY["standard"]

    def renderer(self, **kwargs):
        return self.definition.renderer_class(self.definition, configured(self.definition, **kwargs))

    def test_no_override_css_at_defaults(self):
        self.assertEqual(self.renderer().override_css(), "")

    def test_summary_rows_share_one_font_size(self):
        # Total Payable / Paid Amount / Balance Due print at the 10pt of the other
        # summary rows (legacy CSS: 18pt / 14pt / 14pt), for every document type.
        for document_type in DOCUMENT_TYPES:
            with self.subTest(document_type=document_type):
                css = self.renderer().render(sample_context(document_type, ["T1"])).css
                summary = css.split("/* summary */", 1)[1]
                for row in ("grand-total-row", "paid-amount-row", "balance-due-row"):
                    for part in ("", " .total-label", " .total-value"):
                        self.assertIn(f".{row}{part}", summary.split("{", 1)[0])
                self.assertIn("{ font-size: 10pt; }", summary)
                # Appended after the unchanged legacy stylesheet, so it wins.
                self.assertTrue(css.startswith(BasePDFService.get_base_css(first_page=True, multi_page=True)))

    def test_summary_labels_and_values_match_classic(self):
        # Same rows, labels and values as Classic / Simple for every document type.
        classic = REGISTRY["classic"]
        for document_type, context in real_contexts().items():
            with self.subTest(document_type=document_type):
                standard_rows = [
                    (e["label"], e["row"].value) for e in self.renderer().totals(context)
                    if e["row"].kind not in ("divider", "note")
                ]
                classic_rows = [
                    (e["label"], e["row"].value)
                    for e in classic.renderer_class(classic, classic.defaults).totals(context)
                    if e["row"].kind not in ("divider", "note")
                ]
                self.assertEqual(standard_rows, classic_rows)
        html = self.renderer().render(real_contexts()["sale_gst"]).main_html
        for label in ("Sub Total", "CGST (2.5%)", "Total Amount Due", "Amount Paid", "Balance Due"):
            self.assertIn(f'<span class="total-label">{label}</span>', html)

    def test_outer_padding(self):
        css = self.renderer().render(
            sample_context("booking", ["T1"])
        ).css
        padding = css.split("/* page padding */", 1)[1].split("/* summary */", 1)[0]
        self.assertIn(".content { padding-top: 80px; padding-left: 60px; padding-right: 60px; }", padding)
        self.assertIn("@page { padding-top: 80px; padding-bottom: 40px; }", padding)
        self.assertTrue(css.startswith(BasePDFService.get_base_css(first_page=True, multi_page=True)))

    def test_padding_in_pdf_and_terms_above_bottom_space(self):

        def boxes(document, css_class):
            found = []

            def walk(box, page_index):
                element = getattr(box, "element", None)
                if element is not None and css_class in (element.get("class") or "").split():
                    found.append((page_index, box.position_x, box.position_y, box.position_y + box.margin_height()))
                for child in getattr(box, "children", None) or []:
                    walk(child, page_index)

            for index, page in enumerate(document.pages):
                walk(page._page_box, index)
            return found

        short = render_document(
            self.definition, self.definition.defaults, sample_context("booking", ["Short term."]),
        )
        self.assertEqual(len(short.pages), 1)
        (_, x, _, _), = boxes(short, "main-content")
        self.assertAlmostEqual(x, 60, delta=1)
        (page_index, _, _, terms_bottom), = boxes(short, "terms")
        self.assertEqual(page_index, 0)
        self.assertGreaterEqual(short.pages[0].height - terms_bottom, 40)

        # Terms too long for the first page move to the next one, below the 80px top padding.
        long_terms = [f"Term number {i} with enough words to wrap across the narrower line width." for i in range(18)]
        long = render_document(
            self.definition, self.definition.defaults, sample_context("booking", long_terms),
        )
        (page_index, _, terms_top, terms_bottom), = boxes(long, "terms")
        self.assertEqual((len(long.pages), page_index), (2, 1))
        self.assertGreaterEqual(terms_top, 80)
        self.assertGreaterEqual(long.pages[1].height - terms_bottom, 40)

    def test_each_color_reaches_its_elements(self):
        css = self.renderer(colors={
            "primary": "#112233", "text": "#223344", "border": "#334455", "cancelled": "#445566",
        }).override_css()
        self.assertIn(".shop-name { color: #112233; }", css)                      # primary
        self.assertIn("body { color: #223344; }", css)                            # text
        self.assertIn("table { border-color: #334455; }", css)                    # border/lines
        self.assertIn("th { background-color: #CCD0D4; }", css)                   # light tint for header fill
        self.assertIn(".cancelled-row .total-label, .cancelled-row .total-value { color: #445566; }", css)
        self.assertNotIn("balance-due-row", css)                                  # balance red stays fixed

    def test_background_and_primary_reach_the_page(self):
        default_css = self.renderer().page_css()
        self.assertNotIn("#ABCDEF", default_css)
        css = self.renderer(colors={"background": "#ABCDEF"}).page_css()
        self.assertIn("background: #ABCDEF url(", css)
        self.assertNotEqual(self.renderer(colors={"primary": "#008080"}).page_css(), default_css)  # SVG recolored

    def test_font_applies_everywhere(self):
        renderer = self.renderer(font="dejavu_sans")
        css = renderer.override_css()
        self.assertIn("body { font-family: 'BB DejaVu Sans'", css)
        self.assertIn(".amount-cell { font-family: 'BB DejaVu Sans'", css)
        self.assertIn("'BB DejaVu Sans'", renderer.inline_font("Name", True))

    def test_labels_and_sizes_are_fixed(self):
        context = sample_context("sale", [])
        renderer = self.renderer(colors={"primary": "#000000"})
        parts = renderer.render(context)
        self.assertIn(">ITEM</th>", parts.main_html)
        self.assertIn('SALE <span class="highlight">INFO:</span>', parts.main_html)
        self.assertNotIn("font-size", renderer.override_css())


class ClassicRenderingTests(SimpleTestCase):
    definition = REGISTRY["classic"]

    def render_booking(self, n_items, **kwargs):
        booking, items = make_booking(n_items)
        with mock.patch("shop.models.ShopNumberingSequence.objects", numbering_sequence_mock()):
            context = build_booking_context(make_shop(), make_client(), booking, items, terms=["T1"])
        return context, configured(self.definition, **kwargs)

    def test_long_booking_spans_pages_with_repeating_header(self):
        context, config = self.render_booking(40)
        parts = self.definition.renderer_class(self.definition, config).render(context)
        self.assertIn("display: table-header-group", parts.css)
        self.assertIn("<thead>", parts.main_html)
        self.assertGreaterEqual(len(render_document(self.definition, config, context).pages), 2)

    def test_fixed_reference_design(self):
        context, config = self.render_booking(2)
        parts = self.definition.renderer_class(self.definition, config).render(context)
        self.assertIn("Total Rental amount", parts.main_html)
        self.assertIn("Total Amount Due", parts.main_html)
        self.assertIn("Powered by Bookie Buddy", parts.after_html)
        self.assertIn("Product Measurements", parts.after_html)
        self.assertIn("font-family: 'BB Cormorant Garamond'", parts.css)  # title font fixed
        self.assertIn("font-size: 32pt", parts.css)             # title size fixed

    def test_six_values_reach_css(self):
        context, config = self.render_booking(2, colors={
            "background": "#FFFFFF", "primary": "#112233", "text": "#223344",
            "border": "#334455", "cancelled": "#445566",
        }, font="dejavu_sans")
        parts = self.definition.renderer_class(self.definition, config).render(context)
        self.assertIn("background: #FFFFFF", parts.page_css)
        self.assertIn("color: #112233", parts.css)
        self.assertIn("color: #223344", parts.css)
        self.assertIn("border: 1px solid #334455", parts.css)
        self.assertIn("color: #445566", parts.css)
        self.assertIn("font-family: 'BB DejaVu Sans'", parts.css)
        self.assertIn("font-family: 'BB Cormorant Garamond'", parts.css)  # title stays Cormorant Garamond


class ClassicReferenceLayoutTests(SimpleTestCase):
    """Classic-only layout fixes (padding, icons, logo, name weight, spacing, separator)."""
    classic = REGISTRY["classic"]
    simple = REGISTRY["simple"]

    def context(self, kind="booking", **kwargs):
        with mock.patch("shop.models.ShopNumberingSequence.objects", numbering_sequence_mock()):
            if kind == "sale":
                return build_sale_context(make_shop(), make_client(), *make_sale(2), terms=["T1"])
            booking, items = make_booking(2, **kwargs)
            return build_booking_context(make_shop(), make_client(), booking, items, terms=["T1"])

    def kinds(self, definition, context):
        renderer = definition.renderer_class(definition, definition.defaults)
        return [entry["row"].kind for entry in renderer.totals(context)]

    def test_css_and_markup(self):
        parts = self.classic.renderer_class(self.classic, self.classic.defaults).render(self.context())
        css = re.sub(r"\s+", " ", parts.css)
        self.assertIn("padding: 56px 76px 10px 76px;", css)
        self.assertIn(".footer-content { padding-bottom: 48px;", css)
        self.assertIn("@page { padding-top: 56px; }", css)
        self.assertIn(".c-icon { width: 13px; height: 13px;", css)
        # Item-table header widths come from CSS classes, not a templated inline style.
        for key, width in (("no", "52px"), ("qty", "70px"), ("price", "80px"), ("amount", "90px")):
            self.assertIn(f".c-items th.col-{key} {{ width: {width}; }}", css)
        header = parts.main_html.split("<thead>", 1)[1].split("</thead>", 1)[0]
        self.assertNotIn("style=", header)
        self.assertIn('<th class="col-no">', header)
        self.assertIn('<th class="col-qty center">', header)
        self.assertIn(".c-logo { max-width: 220px; max-height: 180px; margin-top: -56px; }", css)
        self.assertIn(".c-summary { width: 100%; margin-top: 16px;", css)
        self.assertIn("<div>Imani Olowe</div>", parts.main_html)  # customer name in normal weight
        self.assertNotIn('<div class="c-strong">Imani Olowe</div>', parts.main_html)

    def test_separator_below_balance_due(self):
        kinds = self.kinds(self.classic, self.context())  # booking with collected security
        self.assertNotIn("divider", kinds[kinds.index("grand"):kinds.index("paid")])
        self.assertEqual(kinds[kinds.index("balance") + 1], "divider")
        self.assertEqual(kinds.count("divider"), 1)
        sale = self.kinds(self.classic, self.context("sale"))
        self.assertEqual(sale[sale.index("balance") + 1:], ["divider"])
        self.assertNotIn("divider", self.kinds(self.classic, self.context(cancelled=True)))

    def test_simple_keeps_its_own_design(self):
        # Simple shares Classic's data shaping but none of Classic's CSS.
        css = self.simple.renderer_class(self.simple, self.simple.defaults).css()
        self.assertNotIn(".c-icon", css)
        self.assertNotIn("max-height: 180px", css)
        self.assertNotIn(".c-items", css)


class SimpleRenderingTests(SimpleTestCase):
    definition = REGISTRY["simple"]

    def render(self, builder, **kwargs):
        with mock.patch("shop.models.ShopNumberingSequence.objects", numbering_sequence_mock()):
            context = builder()
        renderer = self.definition.renderer_class(self.definition, configured(self.definition, **kwargs))
        return context, renderer.render(context)

    def booking(self, n_items=2, cancelled=False):
        booking, items = make_booking(n_items, cancelled=cancelled)
        return lambda: build_booking_context(make_shop(), make_client(), booking, items, terms=["T1"])

    def test_cancelled_label_below_shop_details_not_in_totals(self):
        for builder in (self.booking(cancelled=True),
                        lambda: build_tailor_context(make_shop(), make_client(), *make_tailor_order(cancelled=True))):
            context, parts = self.render(builder)
            self.assertTrue(context.is_cancelled)
            self.assertIn('<div class="s-cancelled-row"><div class="s-cancelled">CANCELLED</div></div>', parts.main_html)
            self.assertNotIn("cancelled", {row["row"].kind for row in self.definition.renderer_class(
                self.definition, self.definition.defaults).totals(context)})
            # Centred block right after the shop header, before customer / invoice details.
            html = parts.main_html
            self.assertLess(html.index('class="s-header"'), html.index("s-cancelled-row"))
            self.assertLess(html.index("s-cancelled-row"), html.index('class="s-meta"'))
            self.assertNotIn('<td class="center">', html.split('class="s-meta"', 1)[1].split("</table>", 1)[0])

    def test_reference_layout(self):
        context, parts = self.render(self.booking())
        css = re.sub(r"\s+", " ", parts.css)
        self.assertIn("padding: 56px 76px 10px 76px;", css)                 # outer margins
        self.assertIn(".footer-content { padding-bottom: 56px;", css)
        self.assertIn("@page { padding-top: 56px; }", css)
        self.assertIn(".s-logo { width: 84px; height: 84px;", css)           # larger logo
        self.assertRegex(css, r"\.s-shop-details \{[^}]*color: #000000;")    # black shop details
        self.assertRegex(css, r"\.s-item-spec \{[^}]*color: #000000;")       # black product details
        self.assertIn("padding: 12px 6px;", css)                             # roomier product rows
        self.assertIn(".s-summary { width: 100%; margin-top: 16px;", css)    # space above Booking Details
        for key, width in (("no", "52px"), ("qty", "70px"), ("price", "80px"), ("amount", "90px")):
            self.assertIn(f".s-items th.col-{key} {{ width: {width}; }}", css)
        self.assertNotIn("style=", parts.main_html.split("<thead>", 1)[1].split("</thead>", 1)[0])
        # Customer name in normal weight (invoice and measurements page); invoice no. stays bold.
        self.assertIn("<div>Imani Olowe</div>", parts.main_html)
        self.assertNotIn('<div class="s-strong">Imani Olowe</div>', parts.main_html)
        self.assertIn("<div>Imani Olowe</div>", parts.after_html)
        self.assertNotIn('<div class="s-strong">Imani Olowe</div>', parts.after_html)
        self.assertIn('<span class="s-strong">', parts.main_html)
        # Separator below Balance Due, none between Total Amount Due and Amount Paid.
        kinds = [entry["row"].kind for entry in self.definition.renderer_class(
            self.definition, self.definition.defaults).totals(context)]
        self.assertNotIn("divider", kinds[kinds.index("grand"):kinds.index("paid")])
        self.assertEqual(kinds[kinds.index("balance") + 1], "divider")
        self.assertEqual(kinds.count("divider"), 1)

    def test_no_label_when_not_cancelled(self):
        _, parts = self.render(self.booking())
        self.assertNotIn('class="s-cancelled"', parts.main_html)

    def test_fixed_reference_design(self):
        _, parts = self.render(self.booking())
        for size in ("30pt", "15pt", "8pt", "12pt", "6pt", "10pt"):
            self.assertIn(f"font-size: {size}", parts.css)
        self.assertIn(">INVOICE</div>", parts.main_html)
        self.assertIn("CUSTOMER", parts.main_html)
        self.assertIn("BOOKING DETAILS:", parts.main_html)
        self.assertIn("Total Amount Due", parts.main_html)
        self.assertIn("TERMS AND CONDITIONS", parts.footer_html)
        self.assertIn("Powered by Bookie Buddy", parts.after_html)
        self.assertIn("Product Measurements", parts.after_html)
        self.assertIn("<div>GST : 32ABCDE1234F1Z5</div>", parts.main_html)
        self.assertNotIn("<svg", parts.main_html)                      # no icons
        self.assertIn("font-family: 'BB Inter'", parts.css)

    def test_six_values_reach_css(self):
        _, parts = self.render(self.booking(), colors={
            "background": "#FAFAFA", "primary": "#112233", "text": "#223344",
            "border": "#334455", "cancelled": "#445566",
        }, font="dejavu_sans")
        self.assertIn("background: #FAFAFA", parts.page_css)
        self.assertIn("border-bottom: 1.5px solid #112233", parts.css)  # header rule
        self.assertIn("color: #112233", parts.css)                      # grand total, T&C title
        self.assertIn("color: #223344", parts.css)
        self.assertIn("border-bottom: 1px solid #334455", parts.css)
        self.assertIn("color: #445566", parts.css)
        self.assertIn("font-family: 'BB DejaVu Sans'", parts.css)       # chosen font first, everywhere
        self.assertNotIn("font-family: 'BB Inter'", parts.css)

    def test_long_booking_spans_pages_with_repeating_header(self):
        context, parts = self.render(self.booking(40))
        self.assertIn("display: table-header-group", parts.css)
        self.assertGreaterEqual(
            len(render_document(self.definition, self.definition.defaults, context).pages), 2)


def _fake_download(url, *args, **kwargs):
    return LOGO if url == "https://s3/logo.png" else None


class ShopLogoTests(SimpleTestCase):
    """The PDF logo is always the shop's own Shop.img - real invoices and previews."""

    def contexts(self, img):
        shop = make_shop(img=img)
        booking, items = make_booking(1)
        with mock.patch("whatsapp_integration.v3.pdf_template.BasePDFService.download_image",
                        side_effect=_fake_download) as download:
            with mock.patch("shop.models.ShopNumberingSequence.objects", numbering_sequence_mock()):
                real = build_booking_context(shop, make_client(), booking, items)
                preview = preview_context("sale", shop)
        return real, preview, download

    def test_logo_from_shop_img_in_every_template(self):
        real, preview, download = self.contexts("https://s3/logo.png")
        download.assert_any_call("https://s3/logo.png")
        for code, definition in REGISTRY.items():
            renderer = definition.renderer_class(definition, definition.defaults)
            for context in (real, preview):
                self.assertIn(f'src="{LOGO}"', renderer.render(context).main_html, code)

    def test_no_logo_when_shop_has_none(self):
        real, preview, _ = self.contexts(None)
        standard, classic = REGISTRY["standard"], REGISTRY["classic"]
        self.assertIn("No Logo", standard.renderer_class(standard, standard.defaults).render(real).main_html)
        self.assertNotIn("c-logo-cell", classic.renderer_class(classic, classic.defaults).render(preview).main_html)
        simple = REGISTRY["simple"]
        self.assertNotIn("s-logo-cell", simple.renderer_class(simple, simple.defaults).render(preview).main_html)


class SecurityTests(SimpleTestCase):
    def test_user_data_is_escaped(self):
        booking, items = make_booking(1, item_name='<img src="http://evil">')
        shop = make_shop(name="<script>x</script>")
        with mock.patch("shop.models.ShopNumberingSequence.objects", numbering_sequence_mock()):
            context = build_booking_context(shop, make_client(client_name="<b>Bob</b>"), booking, items)
        for code, definition in REGISTRY.items():
            parts = definition.renderer_class(definition, definition.defaults).render(context)
            html = parts.main_html + parts.after_html
            self.assertNotIn('<img src="http://evil"', html, code)
            self.assertNotIn("<script>", html, code)
            self.assertNotIn("<b>Bob</b>", html, code)

    def test_url_fetcher_blocks_network_resources(self):
        for url in ("http://169.254.169.254/latest", "https://example.com/a.png", "ftp://x/y"):
            with self.assertRaises(ValueError):
                _safe_url_fetcher(url)


class ContextHelperTests(SimpleTestCase):
    def test_parse_info_lines(self):
        lines = parse_info_lines(["Pickup: 16/06/2025", "<strong>Meals:</strong> Breakfast", "plain"])
        self.assertEqual((lines[0].label, lines[0].value, lines[0].bold_label), ("Pickup", "16/06/2025", False))
        self.assertEqual((lines[1].label, lines[1].value, lines[1].bold_label), ("Meals", "Breakfast", True))
        self.assertEqual((lines[2].label, lines[2].value), ("", "plain"))

    def test_tax_rows_match_existing_html_builder(self):
        cases = [
            ("GST", 18, "99.99", True, False),
            ("gst", 5, "50", True, False),
            ("VAT", 12.5, "125", True, False),
            ("GST", 18, "180", True, True),
            ("GST", 18, "180", False, False),
        ]
        fmt = BasePDFService.format_currency
        for args in cases:
            html = BasePDFService.build_tax_rows_html(*args, fmt)
            expected = re.findall(r'total-label">(.*?)</span>\s*<span class="total-value">(.*?)</span>', html)
            rows = [(row.label, row.value) for row in tax_rows(*args)]
            self.assertEqual(rows, expected, args)


PEOPLE = [
    {"name": "Person 1", "value": {"chest": "36", "armhole": "16", "upper_arm": "12", "shoulder": "15"}},
    {"name": "Person 2", "value": "{Chest: 38, Armhole: 17, Upper Arm: 13, Shoulder: 16}"},
    {"name": "Person 3", "value": {"chest": "40", "armhole": "18", "upper_arm": "14", "shoulder": "17"}},
    {"name": "Person 4", "value": {"chest": "42", "armhole": "19", "upper_arm": "15", "shoulder": "18"}},
    {"name": "Rahul", "value": {"chest": "44", "armhole": "20", "shoulder": "19"}},
    {"name": "", "value": {"chest": "46", "armhole": "21", "upper_arm": "17", "shoulder": "20"}},
]


class BookingPriceAndPersonMeasurementTests(SimpleTestCase):
    """Booking invoices in every template: Price (= amount / qty) after Qty, and
    per-person measurements as rows x up to 4 person columns per table."""

    def booking_context(self):
        booking, items = make_booking(3)
        items[1].quantity, items[1].amount = 2, Decimal("1000")
        items[2].quantity = 0
        items[2].measurements = PEOPLE
        with mock.patch("shop.models.ShopNumberingSequence.objects", numbering_sequence_mock()):
            return build_booking_context(make_shop(), make_client(), booking, items, terms=["T1"])

    def render(self, code, context):
        definition = REGISTRY[code]
        return definition.renderer_class(definition, definition.defaults).render(context)

    def test_unit_price(self):
        self.assertEqual(_unit_price(Decimal("1000"), 1), Decimal("1000"))
        self.assertEqual(_unit_price(Decimal("1000"), 2), Decimal("500"))
        self.assertEqual(BasePDFService.format_currency(_unit_price(Decimal("1000"), 3)), "₹333.33")
        for quantity in (0, -1, None, "x"):
            self.assertEqual(_unit_price(Decimal("1000"), quantity), Decimal("1000"), quantity)

    def test_price_column_after_qty(self):
        context = self.booking_context()
        self.assertEqual([c.key for c in context.columns], ["no", "item", "qty", "price", "amount"])
        self.assertEqual([i.price for i in context.items], ["₹1,500.00", "₹500.00", "₹1,500.00"])
        headings = {"standard": ("QTY", "PRICE", "AMOUNT"), "classic": ("Quantity", "Price", "Amount"),
                    "simple": ("Quantity", "Price", "Amount")}
        for code, (qty, price, amount) in headings.items():
            with self.subTest(code=code):
                html = self.render(code, context).main_html
                self.assertLess(html.index(f">{qty}</th>"), html.index(f">{price}</th>"))
                self.assertLess(html.index(f">{price}</th>"), html.index(f">{amount}</th>"))
                self.assertIn(">₹500.00</td>", html)

    def test_sale_and_tailor_columns_unchanged(self):
        from invoice_templates.v3.context.sale import COLUMNS as SALE_COLUMNS
        from invoice_templates.v3.context.tailor import COLUMNS as TAILOR_COLUMNS

        self.assertEqual([c.key for c in SALE_COLUMNS], ["no", "item", "price", "qty", "amount"])
        self.assertEqual([c.key for c in TAILOR_COLUMNS], ["no", "item", "qty", "amount"])

    def test_person_measurement_tables(self):
        context = self.booking_context()
        templates = (("standard", "measurements-table", "#6C5CE7"),
                     ("classic", "c-measure-table", "#2B2B2B"), ("simple", "s-measure-table", "#7C3AED"))
        for code, table_class, primary in templates:
            with self.subTest(code=code):
                parts = self.render(code, context)
                html = parts.after_html
                # Key-value card and per-person tables: one table class, shared
                # fixed widths at 80% (180px name column + 96px per value/person).
                self.assertRegex(html, rf'<table class="{table_class}" style="width: ?276px;">')
                self.assertEqual(len(re.findall(rf'<table class="{table_class}" style="width: ?(564|372)px;">', html)), 2)
                self.assertEqual(len(re.findall(r'<col style="width: ?180px;">', html)), 3)
                self.assertEqual(len(re.findall(r'<col style="width: ?96px;">', html)), 7)
                self.assertEqual(len(re.findall(r">\s*Measurements\s*</th>", html)), 2)
                self.assertEqual(html.count('<th class="measurement-name">'), 3)
                self.assertEqual(html.count("person-col"), 6)
                self.assertIn("Rahul", html)
                self.assertIn("Person 6", html)
                self.assertNotIn("S. No.", html)
                self.assertIn("Size</th>", html)  # key-value measurements keep their cards
                css = re.sub(r"\s+", " ", parts.css)
                self.assertEqual(html.count("size-col"), 1)  # the key-value card's Size header
                self.assertIn(
                    f".{table_class} th.measurement-name, .{table_class} th.size-col, "
                    f".{table_class} th.person-col {{ color: {primary}; }}", css)
                if code == "standard":
                    # 80% of 11pt cells / 14pt headers / 11px 9px padding.
                    self.assertIn(".measurements-table { table-layout: fixed; font-size: 8.8pt; }", css)
                    self.assertIn(".measurements-table th { font-size: 11.2pt; padding: 8.8px 7.2px; }", css)
                    self.assertIn(".measurements-table td { padding: 8.8px 7.2px; }", css)
                else:
                    # 80% of the 10pt body font and 6px 7px padding.
                    self.assertIn(f".{table_class} {{ width: 100%; table-layout: fixed; font-size: 8pt; }}", css)
                    self.assertIn("padding: 4.8px 5.6px;", css)
                    # Per-person cards share the key-value cards' 6px grid inset.
                    prefix = code[0]
                    self.assertIn(f".{prefix}-measurements > .{prefix}-measure-card {{ margin-left: 6px; }}", css)

    def test_standard_custom_primary_recolors_person_headings(self):
        definition = REGISTRY["standard"]
        renderer = definition.renderer_class(definition, configured(definition, colors={"primary": "#112233"}))
        css = re.sub(r"\s+", " ", renderer.render(self.booking_context()).css)
        self.assertIn(
            ".measurements-table th.measurement-name, .measurements-table th.size-col, "
            ".measurements-table th.person-col { color: #112233; }", css)


class PreviewFilenameTests(SimpleTestCase):
    def test_names(self):
        from invoice_templates.api.v3.views.mixins import preview_filename

        self.assertEqual(preview_filename("Classic", "booking"), "Invoice_Preview_Classic_Booking.pdf")
        self.assertEqual(preview_filename("Wedding Season", "tailor_order"),
                         "Invoice_Preview_Wedding_Season_Tailor_Order.pdf")
        # Shop-chosen names can never break the Content-Disposition header.
        name = preview_filename('<b>Wed"ding</b> / മല', "sale")
        self.assertEqual(name, "Invoice_Preview_bWeddingb_Sale.pdf")
        self.assertEqual(preview_filename("", "sale"), "Invoice_Preview_Sale.pdf")
