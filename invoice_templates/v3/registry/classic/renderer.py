"""
Classic template renderer (reference design).

CSS is generated from validated settings only (colors are #RRGGBB, sizes
are bounded numbers, fonts come from FONT_REGISTRY), so no user input can
reach the stylesheet unchecked. Markup is a Django template (autoescaped).
"""
from django.template.loader import render_to_string

from whatsapp_integration.v3.pdf_template import BasePDFService

from ...context.base import (
    ROW_BALANCE, ROW_CANCELLED, ROW_DIVIDER, ROW_GRAND, ROW_NOTE, ROW_PAID, TotalRow,
)
from ..base import WATERMARK_TEXT, BaseRenderer, RenderParts
from ..fonts import font_stack
from ..measurements import measurement_layout, scaled

S = BasePDFService

TOTAL_LABEL_KEYS = (
    "product_cost", "room_price", "sub_total", "product_total", "discount", "security",
    "grand_total", "paid", "balance", "security_collected", "security_deduction",
    "security_refunded", "refunded",
)

# Fixed parts of the Classic design (reference spec). Only the five colors and
# the body font come from the shop's settings - see ClassicRenderer.design().
FIXED_DESIGN = {
    "font.title": "cormorant_garamond",
    "size.title": 32, "size.shop_name": 15, "size.cancelled": 13, "size.section_heading": 11,
    "size.product_spec": 7, "size.grand_total": 13, "size.watermark": 7, "size.body": 10,
    "align.title": "center",
    "layout.logo_position": "right",
    "color.text_secondary": "#4A4A4A",
    "label.title": "INVOICE", "label.customer": "Customer", "label.invoice_no": "Invoice No:",
    "label.attender": "Attender:",
    "label.col_no": "S. No.", "label.col_item": "Item", "label.col_price": "Price",
    "label.col_qty": "Quantity", "label.col_amount": "Amount",
    "label.booking_info": "Booking Details", "label.sale_info": "Sale Details", "label.work_info": "Order Details",
    "label.terms_title": "Terms and Conditions",
    "label.total.product_cost": "Total Rental amount", "label.total.room_price": "Room Price",
    "label.total.sub_total": "Sub Total", "label.total.product_total": "Product Total",
    "label.total.discount": "Charges Discount", "label.total.security": "Security Deposit",
    "label.total.grand_total": "Total Amount Due", "label.total.paid": "Amount Paid",
    "label.total.balance": "Balance Due", "label.total.security_collected": "Security Collected",
    "label.total.security_deduction": "Deduction", "label.total.security_refunded": "Security Refunded",
    "label.total.refunded": "Refunded",
    "show.item_images": False, "show.product_code": True, "show.gst": True,
    "show.attender": True, "show.watermark": True,
    "label.watermark": WATERMARK_TEXT,
}

COLUMN_LABEL_KEYS = {
    "no": "label.col_no",
    "item": "label.col_item",
    "price": "label.col_price",
    "qty": "label.col_qty",
    "amount": "label.col_amount",
}

# Fixed item-table column widths (the item column takes the rest). Classic sets
# them with .col-<key> CSS rules (no inline style); Simple uses them inline.
COLUMN_WIDTHS = {"no": "52px", "qty": "70px", "price": "80px", "amount": "90px"}

# Small monochrome icons for the shop contact lines (inline SVG paths).
ICONS = {
    "phone": "M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z",
    "email": "M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm9 7 8-5H4l8 5zm0 2-8-5v8h16V9l-8 5z",
    "location": "M12 2a7 7 0 0 1 7 7c0 5.25-7 13-7 13S5 14.25 5 9a7 7 0 0 1 7-7zm0 9.5A2.5 2.5 0 1 0 12 6.5a2.5 2.5 0 0 0 0 5z",
    "gst": "M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zm-3.5 5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm7 7a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zM16 7.6 7.6 16l1.4 1.4L17.4 9z",
}


def divider_below_balance(totals):
    """
    Classic reference layout: no separator between Total Amount Due and Amount
    Paid; exactly one separator right below Balance Due (reusing the one before
    the security block when there is one). Values and order are unchanged.
    """
    rows = []
    for row in totals:
        if row.kind == ROW_DIVIDER and rows and rows[-1].kind in (ROW_GRAND, ROW_NOTE, ROW_DIVIDER):
            continue
        rows.append(row)
        if row.kind == ROW_BALANCE:
            rows.append(TotalRow(ROW_DIVIDER, "divider"))
    return rows


class ClassicRenderer(BaseRenderer):
    # Subclasses (e.g. Simple) reuse the data shaping with their own design and markup.
    FIXED_DESIGN = FIXED_DESIGN
    template_dir = "classic"

    def design(self) -> dict:
        """Fixed design values + the shop's five colors and font."""
        return {
            **self.FIXED_DESIGN,
            "font.body": self.font,
            "color.page_background": self.color("background"),
            "color.table_header_bg": self.color("background"),
            "color.primary": self.color("primary"),
            "color.title": self.color("primary"),
            "color.text_primary": self.color("text"),
            "color.table_border": self.color("border"),
            "color.divider": self.color("border"),
            "color.cancelled": self.color("cancelled"),
        }

    def cfg(self, key):
        return self.design()[key]

    def css(self) -> str:
        c = self.design().__getitem__
        body = font_stack(c("font.body"))
        title = font_stack(c("font.title"))
        column_widths = " ".join(
            f".c-items th.col-{key} {{ width: {width}; }}" for key, width in COLUMN_WIDTHS.items()
        )
        return f"""
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        @page {{ size: A4; margin: 0; }}
        @page {{ padding-top: 56px; }}
        @page :first {{ padding-top: 0; }}

        body {{
            font-family: {body};
            font-size: {c("size.body")}pt;
            line-height: 1.4;
            color: {c("color.text_primary")};
            background: transparent;
        }}
        .content {{ position: relative; display: block; min-height: 297mm; padding: 56px 76px 10px 76px; }}
        .footer-content {{ padding-bottom: 48px; page-break-inside: avoid; break-inside: avoid; }}

        table {{ border-collapse: collapse; }}
        .c-header {{ width: 100%; }}
        .c-header td {{ vertical-align: top; padding: 0; border: none; }}
        .c-shop-name {{
            font-size: {c("size.shop_name")}pt; font-weight: 700; letter-spacing: 1px;
            text-transform: uppercase; color: {c("color.primary")}; margin: 6px 0 6px 0;
        }}
        .c-contact {{ color: {c("color.text_secondary")}; line-height: 1.55; }}
        .c-contact div {{ white-space: normal; }}
        .c-icon {{ width: 13px; height: 13px; margin-right: 6px; vertical-align: -3px; }}
        .c-logo-cell {{ width: 230px; }}
        .c-logo-cell.right {{ text-align: right; }}
        .c-logo-cell.left {{ text-align: left; }}
        .c-logo {{ max-width: 220px; max-height: 180px; margin-top: -56px; }}

        .c-title {{
            font-family: {title}; font-size: {c("size.title")}pt; font-weight: 400;
            text-align: {c("align.title")}; color: {c("color.title")};
            letter-spacing: 1px; margin: 10px 0 14px 0; line-height: 1.1;
        }}

        .c-meta {{ width: 100%; margin-bottom: 14px; }}
        .c-meta td {{ vertical-align: top; padding: 0; border: none; }}
        .c-meta .right {{ text-align: right; }}
        .c-section-heading {{
            font-size: {c("size.section_heading")}pt; font-weight: 700;
            color: {c("color.primary")}; margin-bottom: 3px;
        }}
        /* Booking invoices: Customer / Booking Details / Terms headings in the text color. */
        .c-section-heading.text-colored {{ color: {c("color.text_primary")}; }}
        .c-muted {{ color: {c("color.text_secondary")}; }}
        .c-strong {{ font-weight: 700; color: {c("color.text_primary")}; }}

        .c-items {{ width: 100%; margin: 6px 0 18px 0; }}
        .c-items thead {{ display: table-header-group; }}
        .c-items tr {{ page-break-inside: avoid; break-inside: avoid; }}
        .c-items th {{
            background-color: {c("color.table_header_bg")};
            border: 1px solid {c("color.table_border")};
            padding: 9px 8px; text-align: left; font-weight: 700; color: {c("color.text_primary")};
        }}
        .c-items td {{ border: 1px solid {c("color.table_border")}; padding: 9px 8px; vertical-align: top; }}
        {column_widths}
        .c-items .center {{ text-align: center; }}
        .c-items .right {{ text-align: right; }}
        .c-item-media {{ border: none; }}
        .c-item-media td {{ border: none; padding: 0; vertical-align: middle; }}
        .c-item-img {{ width: 38px; height: 38px; object-fit: cover; margin-right: 8px; border: 1px solid {c("color.divider")}; }}
        .c-item-spec {{ font-size: {c("size.product_spec")}pt; color: {c("color.text_secondary")}; margin-top: 2px; }}

        .c-summary {{ width: 100%; margin-top: 16px; page-break-inside: avoid; break-inside: avoid; }}
        .c-summary > tbody > tr > td {{ vertical-align: top; padding: 0; border: none; }}
        .c-details-line {{ line-height: 1.6; }}
        .c-totals {{ width: 100%; }}
        .c-totals td {{ padding: 4px 0; border: none; }}
        .c-totals .val {{ text-align: right; white-space: nowrap; }}
        .c-totals .grand td {{
            font-size: {c("size.grand_total")}pt; font-weight: 700; padding: 10px 0 8px 0; color: {c("color.primary")};
        }}
        .c-totals .grand.has-note td {{ padding-bottom: 0; }}
        .c-totals .gap td {{ padding-top: 10px; }}
        .c-totals .divider td {{ border-top: 1px solid {c("color.divider")}; padding: 0; height: 8px; }}
        .c-totals .balance td {{ font-weight: 700; }}
        .c-totals .cancelled td {{
            color: {c("color.cancelled")}; font-size: {c("size.cancelled")}pt; font-weight: 700;
        }}
        .c-totals .note td {{ font-size: 7.5pt; color: {c("color.text_secondary")}; text-align: right; padding-top: 0; }}

        .c-terms {{ page-break-inside: avoid; break-inside: avoid; }}
        .c-terms-list {{ color: {c("color.text_secondary")}; line-height: 1.55; }}

        .c-watermark {{
            position: fixed; bottom: 10px; left: 0; right: 0; text-align: center;
            font-size: {c("size.watermark")}pt; color: {c("color.text_secondary")};
        }}

        .c-measurements {{ margin-top: 24px; }}
        /* Measurement page headings use the text color (the title font is only for "Invoice"). */
        .c-measurements-title {{
            font-size: {c("size.section_heading") * 2}pt;
            text-align: center; color: {c("color.text_primary")}; margin-bottom: 10px;
        }}
        .c-measure-pair {{ width: 100%; margin-bottom: 14px; page-break-inside: avoid; break-inside: avoid; }}
        .c-measure-pair > tbody > tr > td {{ width: 50%; vertical-align: top; padding: 0 6px; border: none; }}
        .c-measure-card {{ page-break-inside: avoid; break-inside: avoid; margin-bottom: 14px; }}
        /* Key-value cards and per-person tables share one style at 80% size; widths are inline. */
        .c-measure-table {{ width: 100%; table-layout: fixed; font-size: {scaled(c("size.body"))}pt; }}
        .c-measure-table th, .c-measure-table td {{
            border: 1px solid {c("color.table_border")}; padding: {scaled(6)}px {scaled(7)}px; text-align: left;
            word-wrap: break-word; overflow-wrap: break-word;
        }}
        .c-measure-table th {{ background-color: {c("color.table_header_bg")}; font-weight: 700; }}
        .c-measure-table .right {{ text-align: right; }}
        .c-measure-table th.measurement-name, .c-measure-table th.size-col,
        .c-measure-table th.person-col {{ color: {c("color.text_primary")}; }}
        /* Per-person cards start at the same left edge as the key-value cards (grid cell inset). */
        .c-measurements > .c-measure-card {{ margin-left: 6px; }}
        """

    def page_css(self) -> str:
        return f"@page {{ background: {self.cfg('color.page_background')}; }}"

    # ---- data shaping ----------------------------------------------------
    def contact_lines(self, context):
        shop = context.shop
        lines = []
        phones = ", ".join(p for p in (shop.phone, shop.phone2) if p)
        if phones:
            lines.append(("phone", phones))
        if shop.email:
            lines.append(("email", shop.email))
        location = ", ".join(p for p in (shop.address, shop.city) if p)
        if shop.pincode:
            location = f"{location} - {shop.pincode}" if location else shop.pincode
        if location:
            lines.append(("location", location))
        if shop.gst_number and self.cfg("show.gst"):
            lines.append(("gst", f"GST : {shop.gst_number}"))
        return [{"icon": ICONS[icon], "text": value} for icon, value in lines]

    def columns(self, context):
        # Reference design: number + item left, quantity centered, money right.
        align = {"qty": "center", "price": "right", "amount": "right"}
        return [
            {
                "key": col.key,
                "label": self.cfg(COLUMN_LABEL_KEYS[col.key]),
                "css_class": align.get(col.key, ""),
                "width": COLUMN_WIDTHS.get(col.key),
            }
            for col in context.columns
        ]

    def items(self, context):
        show_code = self.cfg("show.product_code")
        rows = []
        for item in context.items:
            spec = []
            if item.code_line and show_code:
                spec.append(item.code_line)
            spec.extend(item.details)
            image = item.image.strip() if item.image and item.image.strip().startswith("data:image") else ""
            rows.append({"item": item, "spec": " • ".join(spec), "image": image})
        return rows

    def totals(self, context):
        mapping = {key: self.cfg(f"label.total.{key}") for key in TOTAL_LABEL_KEYS}
        css = {
            ROW_GRAND: "grand", ROW_DIVIDER: "divider", ROW_NOTE: "note",
            ROW_CANCELLED: "cancelled", ROW_BALANCE: "balance", ROW_PAID: "paid",
        }
        totals = context.totals
        if self.template_dir == "classic":  # Classic design only; Simple keeps its own order
            totals = divider_below_balance(totals)
        rows = []
        for index, row in enumerate(totals):
            if row.kind == ROW_CANCELLED:
                label = row.label.title()
            elif row.dynamic:
                label = row.raw_label or row.label
            else:
                label = self.label_for(row, mapping)
            row_css = css.get(row.kind, "")
            if row.section_gap:
                row_css += " gap"
            if row.kind == ROW_GRAND and index + 1 < len(totals) and totals[index + 1].kind == ROW_NOTE:
                row_css += " has-note"  # total due sits close to its "(Inclusive of all taxes.)" note
            rows.append({"row": row, "label": label, "css": row_css.strip()})
        return rows

    def measurement_context(self, context):
        data = context.measurements
        if not data.has_content:
            return None
        # Key-value cards and per-person tables share fixed column widths.
        return measurement_layout(data)

    # ---- render ----------------------------------------------------------
    def template_context(self, context) -> dict:
        return {
            "ctx": context,
            "cfg": self.config,
            "contact_lines": self.contact_lines(context),
            "icon_color": self.cfg("color.text_secondary"),
            "logo_left": self.cfg("layout.logo_position") == "left",
            "show_logo": bool(context.shop.logo),  # the shop's own Shop.img
            "title": self.cfg("label.title"),
            "customer_label": self.cfg("label.customer"),
            "invoice_no_label": self.cfg("label.invoice_no"),
            "attender_label": self.cfg("label.attender"),
            "show_attender": self.cfg("show.attender") and bool(context.staff_name),
            "columns": self.columns(context),
            "items": self.items(context),
            "show_item_images": self.cfg("show.item_images"),
            "details_heading": self.cfg(f"label.{context.info_key}"),
            "totals": self.totals(context),
            "terms_title": self.cfg("label.terms_title"),
            # Classic booking invoices only (unused by Simple's own templates).
            "heading_text_color": context.document_type == "booking",
            "measurements": self.measurement_context(context),
            "show_watermark": self.cfg("show.watermark"),
            "watermark": self.cfg("label.watermark"),
        }

    def render(self, context) -> RenderParts:
        template_context = self.template_context(context)
        base = f"invoice_templates/{self.template_dir}"
        return RenderParts(
            main_html=render_to_string(f"{base}/main_content.html", template_context),
            footer_html=render_to_string(f"{base}/footer_content.html", template_context),
            after_html=render_to_string(f"{base}/after_content.html", template_context),
            css=self.css(),
            page_css=self.page_css(),
            font_keys={self.cfg("font.title"), self.cfg("font.body")},
        )
