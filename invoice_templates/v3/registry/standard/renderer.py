"""
Standard template renderer.

Markup: the existing invoice's structure and class names (Django template,
autoescaped). CSS: the existing BasePDFService.get_base_css() unchanged, plus
an override stylesheet containing ONLY the settings that differ from the
defaults - so with default settings the CSS is exactly the existing CSS.
"""
import base64
import html
import logging
import re
from functools import lru_cache

from django.template.loader import render_to_string
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from whatsapp_integration.v3.pdf_template import BasePDFService

from ...context.base import (
    ROW_BALANCE, ROW_CANCELLED, ROW_GRAND, ROW_LINE, ROW_NOTE, ROW_PAID, ROW_SUBTOTAL,
)
from ..base import WATERMARK_TEXT, BaseRenderer, RenderParts
from ..classic.renderer import FIXED_DESIGN as CLASSIC_DESIGN
from ..classic.renderer import TOTAL_LABEL_KEYS
from ..fonts import font_stack
from ..measurements import measurement_layout, scaled

logger = logging.getLogger("api_requests")

S = BasePDFService

LEGACY_DECORATION_COLOR = "#3D0094"
# Font size of every summary row (legacy .total-row / .subtotal-row / .discount-row).
SUMMARY_FONT_PT = 10
# Outer padding (legacy base CSS: 60px top, 40px sides, ~0-10px bottom).
PAGE_PADDING_TOP_PX = 80      # first page (.content) and later pages (@page)
PAGE_PADDING_SIDE_PX = 60
PAGE_PADDING_BOTTOM_PX = 40   # every page (@page)
# The shared terms placement measures the terms block alone at the legacy
# 40px-side width and reserves only ~10px at the page bottom; see layout_css().
LEGACY_PADDING_SIDE_PX = 40
TERMS_PADDING_BOTTOM_PX = 20  # .footer-content's own bottom padding (legacy CSS)
FIRST_PAGE_SVG = "images/invoice_pdf_layout.svg"
LATER_PAGES_SVG = "images/other_pages.svg"
# The backgrounds' "Powered by www.bookiebuddy.in" is outlined glyphs: their only black path.
_FOOTER_TEXT_PATH = re.compile(rb'<path\b[^>]*\bfill="black"[^>]*/>\s*')
# Its place on the page: the grey corner of the bottom bar (SVG x 381-595, y 820-842
# on a 595x842 viewBox -> A4 px at 793.7/595).
FOOTER_TEXT_LEFT_PX = 508
FOOTER_TEXT_WIDTH_PX = 261   # centred on the original text (page x 554-723, centre 639)
FOOTER_TEXT_HEIGHT_PX = 29   # bottom bar height
FOOTER_TEXT_LINE_PX = 13
FOOTER_TEXT_TOP_PX = 9       # centres the line on the original text (page y 1104-1115)
FOOTER_TEXT_SIZE_PT = 8

ROW_CSS = {
    ROW_SUBTOTAL: "subtotal-row",
    ROW_LINE: "discount-row",
    ROW_GRAND: "grand-total-row",
    ROW_PAID: "paid-amount-row",
    ROW_BALANCE: "balance-due-row",
    ROW_CANCELLED: "cancelled-row",
}

# Fixed labels of the Standard design (the existing invoice's headings).
COLUMN_LABELS = {"no": "NO", "item": "ITEM", "price": "PRICE", "qty": "QTY"}
AMOUNT_LABEL = {"sale": "TOTAL"}  # booking / tailor_order -> "AMOUNT"
INFO_TITLES = {"booking_info": "BOOKING INFO:", "sale_info": "SALE INFO:", "work_info": "WORK INFO:"}
BILL_TO_TITLE = "BILL TO"
TERMS_TITLE = "TERMS AND CONDITIONS"
MUTED_TEXT_COLOR = "#6B7280"
# The existing invoice's inline font stacks, kept verbatim at the default font.
LEGACY_EMPHASIS_STACK = "'Inter', sans-serif"
LEGACY_MALAYALAM_STACK = "'Noto Sans Malayalam', sans-serif"

# Customizable color -> [(selector, property)] for the override stylesheet.
# Only selectors that hardcode the corresponding default in get_base_css.
_COLOR_TARGETS = {
    "primary": [
        (".shop-name", "color"), (".section-title", "color"),
        (".order-info-title .highlight", "color"), (".terms-title .highlight", "color"),
        (".grand-total-row .total-label, .grand-total-row .total-value", "color"),
        (".payment-history-title .highlight", "color"),
    ],
    "text": [
        # body text
        ("body", "color"), (".shop-details", "color"), (".invoice-value", "color"),
        (".bill-to-phone", "color"), (".bill-to-address", "color"), ("td", "color"),
        (".order-info-item", "color"), (".terms-list", "color"),
        (".measurements-table td", "color"), (".person-measurements-table td", "color"),
        # headings / labels / amounts
        (".invoice-label", "color"), (".bill-to-name", "color"), ("th", "color"),
        (".item-name", "color"), (".total-label", "color"), (".total-value", "color"),
        (".terms-title", "color"), (".amount-cell", "color"), (".variant-table-heading", "color"),
        (".measurements-table th", "color"), (".person-measurements-table th", "color"),
    ],
    "border": [
        ("table", "border-color"), ("th", "border-bottom-color"),
        (".grand-total-row", "border-top-color"), (".status-header", "border-top-color"),
        (".variant-table-heading", "border-bottom-color"),
        (".measurements-table, .person-measurements-table", "border-color"),
    ],
    "cancelled": [
        (".cancelled-row .total-label, .cancelled-row .total-value", "color"),
    ],
}

# Table header fills (#D9D9D9 by default, same as the lines).
_HEADER_FILL_SELECTORS = (
    "th", ".measurements-table th, .measurements-table thead", ".person-measurements-table th",
)


def _tint(hex_color: str, strength: float = 0.25) -> str:
    """Mix a #RRGGBB color with white (strength = share of the original color)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    mix = lambda c: round(255 - (255 - c) * strength)  # noqa: E731
    return f"#{mix(r):02X}{mix(g):02X}{mix(b):02X}"


# Elements the existing CSS pins to Inter (names/amounts/totals).
_EMPHASIS_SELECTORS = (
    ".total-value", ".grand-total-row", ".paid-amount-row", ".cancelled-row",
    ".balance-due-row", ".amount-cell",
)


@lru_cache(maxsize=8)
def _svg_bytes(relative_path: str):
    path = S.get_static_path(relative_path)
    if not path:
        logger.warning(f"SVG not found at: {relative_path}")
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        logger.warning(f"Failed to load SVG background {relative_path}: {e}")
        return None


@lru_cache(maxsize=1)
def footer_text_replaceable() -> bool:
    """True when every page background has the outlined "Powered by www.bookiebuddy.in" path."""
    for relative_path in (FIRST_PAGE_SVG, LATER_PAGES_SVG):
        data = _svg_bytes(relative_path)
        if not data or len(_FOOTER_TEXT_PATH.findall(data)) != 1:
            return False
    return True


@lru_cache(maxsize=64)
def _svg_background(relative_path: str, decoration_color: str):
    """SVG page background as a data URI, recolored when not the default.

    The background's outlined "Powered by www.bookiebuddy.in" footer text is
    removed (in memory only - the shared file is unchanged) and printed as
    "Powered by Bookie Buddy" HTML text in the same place instead.
    """
    data = _svg_bytes(relative_path)
    if data is None:
        return None
    if footer_text_replaceable():
        data = _FOOTER_TEXT_PATH.sub(b"", data)
    if decoration_color.upper() != LEGACY_DECORATION_COLOR:
        data = data.replace(LEGACY_DECORATION_COLOR.encode(), decoration_color.encode())
        data = data.replace(LEGACY_DECORATION_COLOR.lower().encode(), decoration_color.encode())
    return "data:image/svg+xml;base64," + base64.b64encode(data).decode("utf-8")


def highlight_last_word(label: str):
    """'BOOKING INFO:' -> 'BOOKING <span class="highlight">INFO:</span>' (escaped)."""
    head, sep, last = label.rpartition(" ")
    if not sep:
        return format_html('<span class="highlight">{}</span>', label)
    return format_html('{} <span class="highlight">{}</span>', head, last)


class StandardRenderer(BaseRenderer):

    # ---- CSS -------------------------------------------------------------
    def override_css(self) -> str:
        """Rules only for values that differ from the defaults (defaults -> no rules -> legacy CSS)."""
        rules = []
        for key, targets in _COLOR_TARGETS.items():
            if not self.is_default(key):
                rules += [f"{sel} {{ {prop}: {self.color(key)}; }}" for sel, prop in targets]
        if not self.is_default("border"):
            # Header fills use a light tint of the border color so header text stays readable.
            fill = _tint(self.color("border"))
            rules += [f"{sel} {{ background-color: {fill}; }}" for sel in _HEADER_FILL_SELECTORS]
        if not self.is_default("font"):
            stack = font_stack(self.font)  # bundled fonts only
            rules.append(f"body {{ font-family: {stack}; }}")
            rules += [f"{sel} {{ font-family: {stack}; }}" for sel in _EMPHASIS_SELECTORS]
        return "\n".join(rules)

    def page_css(self) -> str:
        # The decorative shapes follow the primary color; the background color
        # sits behind them. At defaults this is exactly the existing CSS.
        decoration = LEGACY_DECORATION_COLOR if self.is_default("primary") else self.color("primary")
        background = "" if self.is_default("background") else f"{self.color('background')} "
        first_page_bg = _svg_background(FIRST_PAGE_SVG, decoration)
        later_pages_bg = _svg_background(LATER_PAGES_SVG, decoration)
        css = ""
        if first_page_bg:
            css += f"""
        @page :first {{
            background: {background}url('{first_page_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """
        if later_pages_bg:
            css += f"""
        @page {{
            background: {background}url('{later_pages_bg}') no-repeat center center;
            background-size: 210mm 297mm;
        }}
        """
        return css

    def emphasis_stack(self) -> str:
        # Default font keeps the existing invoice's CSS exactly (Inter for names/amounts).
        return LEGACY_EMPHASIS_STACK if self.is_default("font") else font_stack(self.font)

    # ---- Markup helpers --------------------------------------------------
    def inline_font(self, text_value, bold=False) -> str:
        """Same per-text font choice as BasePDFService.get_font_family/weight."""
        if S.contains_malayalam(str(text_value)) and self.is_default("font"):
            family = LEGACY_MALAYALAM_STACK
        elif S.contains_malayalam(str(text_value)):
            family = font_stack("noto_sans_malayalam")
        else:
            family = self.emphasis_stack()
        weight = "700" if bold else "400"
        return f"font-family: {family}; font-weight: {weight};"

    def bill_to_rows(self, context):
        party = context.party
        sale = context.document_type == "sale"
        rows = []
        if party.show_name:
            rows.append(("bill-to-name", party.name, self.inline_font(party.name, True)))
        # Sale invoices print the phone in the emphasis font (bold).
        rows.append(("bill-to-phone", party.phone, self.inline_font(party.phone, True) if sale else ""))
        if context.document_type != "tailor_order":
            rows.append(("bill-to-address", party.address, self.inline_font(party.address)))
        return rows

    def columns(self, context):
        columns = []
        for column in context.columns:
            label = COLUMN_LABELS.get(column.key) or AMOUNT_LABEL.get(context.document_type, "AMOUNT")
            css_class = {"center": "text-center", "right": "text-right"}.get(column.align, "")
            columns.append({
                "label": label,
                "width": column.width,
                "css_class": css_class,
                "key": column.key,
            })
        return columns

    def items(self, context):
        rows = []
        for item in context.items:
            image_state = "none"
            if item.image:
                image_state = "img" if item.image.strip().startswith("data:image") else "invalid"
            if item.details_inline:
                meta = [item.code_line] if item.code_line else []
                if item.details:
                    meta.append(", ".join(item.details))
            else:
                meta = list(item.details)
            rows.append({
                "item": item,
                "image_state": image_state,
                "image": item.image.strip() if item.image else "",
                "name_style": self.inline_font(item.name, True),
                "meta": meta,
            })
        return rows

    def totals(self, context):
        # Same summary labels as Classic / Simple (their label.total.* names,
        # data labels as entered, "Booking Cancelled"); values and rows unchanged.
        mapping = {key: CLASSIC_DESIGN[f"label.total.{key}"] for key in TOTAL_LABEL_KEYS}
        rows = []
        for index, row in enumerate(context.totals):
            css = ROW_CSS.get(row.kind, "")
            if row.section_gap:
                css += " section-gap"
            if row.kind == ROW_GRAND and _next_is_note(context.totals, index):
                css += " has-note"
            if row.kind == ROW_CANCELLED:
                label = row.label.title()
            elif row.dynamic:
                label = row.raw_label or row.label
            else:
                label = self.label_for(row, mapping)
            rows.append({"row": row, "css": css, "label": label})
        return rows

    def after_html(self, context) -> str:
        """The footer "Powered by" text (every page) + the measurements page when there is one.

        Without a replaceable background text, the background keeps its own
        "Powered by" and nothing is added, so there is always exactly one.
        """
        footer = ""
        if footer_text_replaceable():
            footer = render_to_string("invoice_templates/standard/after_content.html", {"footer_text": WATERMARK_TEXT})
        return footer + self.measurements_html(context)

    def measurements_html(self, context) -> str:
        data = context.measurements
        if not data.has_content:
            return ""
        esc = html.escape
        left = []
        if data.invoice_no:
            left.append(f"<span>Invoice: <strong>{esc(data.invoice_no)}</strong></span>")
        if data.pickup_str:
            left.append(f"<span>Pickup: {esc(data.pickup_str)}</span>")
        right = []
        if data.client_name:
            right.append(f'<strong style="font-size:12pt;">{esc(data.client_name)}</strong>')
        if data.client_phone:
            right.append(f"<span>{esc(data.client_phone)}</span>")
        left_html, right_html = "<br/>".join(left), "<br/>".join(right)
        client_info = (
            '<table style="width:100%;border:none;background:transparent;">'
            '<tr style="background:transparent;">'
            f'<td style="width:50%;border:none;vertical-align:middle;text-align:left;padding:0;">{left_html}</td>'
            f'<td style="width:50%;border:none;vertical-align:middle;text-align:right;padding:0;">{right_html}</td>'
            "</tr></table>"
        ) if (left_html or right_html) else ""
        # Same page structure as the existing invoice, but key-value cards and
        # per-person tables share one table style and fixed column widths.
        layout = measurement_layout(data)
        blocks = [{"kind": "pair", "cards": pair} for pair in layout["pairs"]]
        for person in layout["people"]:
            blocks += [
                {"kind": "person", "heading": person["heading"] if idx == 0 else "", "table": table}
                for idx, table in enumerate(person["tables"])
            ]
        return render_to_string("invoice_templates/standard/after_content.html", {
            "measurements": layout,
            "blocks": blocks,
            "client_info_html": mark_safe(client_info),  # built from escaped values above
            "staff_name": data.staff_name,
        })

    def summary_css(self) -> str:
        """Total Payable / Paid Amount / Balance Due at the same size as the other
        summary rows (legacy CSS prints them at 18pt / 14pt / 14pt)."""
        selectors = ", ".join(
            f".{row}{part}"
            for row in ("grand-total-row", "paid-amount-row", "balance-due-row")
            for part in ("", " .total-label", " .total-value")
        )
        # Total due sits close to its "(Inclusive of all taxes.)" note.
        return (
            f"{selectors} {{ font-size: {SUMMARY_FONT_PT}pt; }}\n"
            ".grand-total-row.has-note { margin-bottom: 0; padding-bottom: 0; }"
        )

    def layout_css(self) -> str:
        """Outer page padding.

        The shared terms placement measures `.footer-content` on its own (no
        `.content` parent) at the legacy 713.7px width, and reserves only
        ~10px at the page bottom. The bare `.footer-content` rule therefore
        narrows the measured block to the real usable width and adds the new
        bottom padding to its measured height; `.content .footer-content`
        keeps the real block exactly as before.
        """
        side_extra = PAGE_PADDING_SIDE_PX - LEGACY_PADDING_SIDE_PX
        top, side, bottom = PAGE_PADDING_TOP_PX, PAGE_PADDING_SIDE_PX, PAGE_PADDING_BOTTOM_PX
        return (
            f".content {{ padding-top: {top}px; padding-left: {side}px; padding-right: {side}px; }}\n"
            f"@page {{ padding-top: {top}px; padding-bottom: {bottom}px; }}\n"
            f".footer-content {{ padding-left: {side_extra}px; padding-right: {side_extra}px; "
            f"padding-bottom: {TERMS_PADDING_BOTTOM_PX + bottom}px; }}\n"
            ".content .footer-content { padding-left: 0; padding-right: 0; "
            f"padding-bottom: {TERMS_PADDING_BOTTOM_PX}px; }}\n"
            # "Powered by" on every page, in the background's grey footer corner
            # (bottom: -padding puts the box on the paper edge).
            f".bb-footer-text {{ position: fixed; bottom: -{bottom}px; left: {FOOTER_TEXT_LEFT_PX}px; "
            f"width: {FOOTER_TEXT_WIDTH_PX}px; height: {FOOTER_TEXT_HEIGHT_PX}px; "
            f"padding-top: {FOOTER_TEXT_TOP_PX}px; line-height: {FOOTER_TEXT_LINE_PX}px; text-align: center; "
            f"font-family: {LEGACY_EMPHASIS_STACK}; font-size: {FOOTER_TEXT_SIZE_PT}pt; color: #000000; }}"
        )

    def measurement_css(self) -> str:
        """Shared measurement table rules (only appended when there is a measurements page)."""
        # 80% of the existing table sizes (11pt cells, 14pt headers, 11px 9px padding).
        padding = f"{scaled(11)}px {scaled(9)}px"
        return (
            f".measurements-table {{ table-layout: fixed; font-size: {scaled(11)}pt; }}\n"
            ".measurements-table th, .measurements-table td "
            "{ word-wrap: break-word; overflow-wrap: break-word; }\n"
            f".measurements-table th {{ font-size: {scaled(14)}pt; padding: {padding}; }}\n"
            f".measurements-table td {{ padding: {padding}; }}\n"
            ".measurements-table th.measurement-name, .measurements-table th.size-col, "
            ".measurements-table th.person-col "
            f"{{ color: {self.color('text')}; }}\n"
            # Page and card headings in the text color too (legacy CSS uses the accent).
            f".measurements-title, .variant-table-heading {{ color: {self.color('text')}; }}"
        )

    # ---- Render ----------------------------------------------------------
    def render(self, context) -> RenderParts:
        shop_name_lines = S.split_text_by_words(context.shop.name).split("<br>") if context.shop.name else []
        template_context = {
            "ctx": context,
            "shop_name_html": format_html_join(mark_safe("<br>"), "{}", ((line,) for line in shop_name_lines)),
            "shop_details_html": format_html_join(
                mark_safe("<br/>"), "{}", ((line,) for line in context.shop.detail_lines)
            ),
            "bill_to_title": BILL_TO_TITLE,
            "bill_to_rows": self.bill_to_rows(context),
            "columns": self.columns(context),
            "items": self.items(context),
            "info_title_html": highlight_last_word(INFO_TITLES[context.info_key]),
            "totals": self.totals(context),
            "muted_color": MUTED_TEXT_COLOR,
            "show_staff": bool(context.staff_name),
            "terms_title_html": highlight_last_word(TERMS_TITLE),
        }
        main_html = render_to_string("invoice_templates/standard/main_content.html", template_context)
        footer_html = render_to_string("invoice_templates/standard/footer_content.html", template_context)
        after_html = self.after_html(context)
        css = S.get_base_css(first_page=True, multi_page=True)
        css += "\n/* page padding */\n" + self.layout_css()
        css += "\n/* summary */\n" + self.summary_css()
        if context.measurements.has_content:
            css += "\n/* measurement tables */\n" + self.measurement_css()
        overrides = self.override_css()
        if overrides:
            css += "\n/* shop customization */\n" + overrides
        return RenderParts(
            main_html=main_html,
            footer_html=footer_html,
            after_html=after_html,
            css=css,
            page_css=self.page_css(),
            # Default font: the legacy stylesheet's own fonts only; otherwise the bundled stack.
            font_keys=set() if self.is_default("font") else {self.font},
        )


def _next_is_note(totals, index):
    """True when the row after `index` is the "(Inclusive of all taxes.)" note."""
    return index + 1 < len(totals) and totals[index + 1].kind == ROW_NOTE
