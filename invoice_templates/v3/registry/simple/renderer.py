"""
Simple template renderer.

Reuses the Classic data shaping (columns, items, totals, measurements) with
its own fixed design, stylesheet and markup. CSS is generated from validated
settings only, markup is a Django template (autoescaped).
"""
from dataclasses import replace

from ...context.base import ROW_CANCELLED
from ..classic.renderer import FIXED_DESIGN as CLASSIC_DESIGN
from ..classic.renderer import COLUMN_WIDTHS, ClassicRenderer, divider_below_balance
from ..fonts import font_stack
from ..measurements import scaled

# Fixed parts of the Simple design (reference spec). Only the five colors and
# the font come from the shop's settings - see ClassicRenderer.design().
FIXED_DESIGN = {
    **CLASSIC_DESIGN,
    "size.title": 30, "size.shop_name": 15, "size.shop_details": 8, "size.cancelled": 12,
    "size.product_spec": 6, "size.grand_total": 12, "size.watermark": 8, "size.body": 10,
    "color.text_secondary": "#6B7280",
    "label.customer": "CUSTOMER",
    "label.booking_info": "BOOKING DETAILS:", "label.sale_info": "SALE DETAILS:",
    "label.work_info": "ORDER DETAILS:",
    "label.terms_title": "TERMS AND CONDITIONS",
    "label.cancelled": "CANCELLED",
}


class SimpleRenderer(ClassicRenderer):
    FIXED_DESIGN = FIXED_DESIGN
    template_dir = "simple"

    def design(self) -> dict:
        # One font for everything, including the title.
        return {**super().design(), "font.title": self.font}

    def css(self) -> str:
        c = self.design().__getitem__
        body = font_stack(c("font.body"))
        column_widths = " ".join(
            f".s-items th.col-{key} {{ width: {width}; }}" for key, width in COLUMN_WIDTHS.items()
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
        .footer-content {{ padding-bottom: 56px; page-break-inside: avoid; break-inside: avoid; }}

        table {{ border-collapse: collapse; }}
        .s-header {{ width: 100%; margin-bottom: 22px; }}
        .s-header td {{ vertical-align: top; padding: 0; border: none; }}
        .s-logo-cell {{ width: 96px; }}
        .s-logo {{ width: 84px; height: 84px; object-fit: contain; }}
        .s-shop-name {{
            font-size: {c("size.shop_name")}pt; font-weight: 600;
            color: {c("color.text_primary")}; line-height: 1.2; margin-bottom: 4px;
        }}
        .s-shop-details {{ font-size: {c("size.shop_details")}pt; color: #000000; line-height: 1.5; }}
        .s-title-cell {{ text-align: right; width: 200px; }}
        .s-title {{
            font-size: {c("size.title")}pt; font-weight: 400; line-height: 1;
            color: {c("color.text_primary")}; letter-spacing: 0.5px;
        }}

        .s-meta {{ width: 100%; margin-bottom: 18px; }}
        .s-meta td {{ vertical-align: top; padding: 0; border: none; }}
        .s-meta .left {{ width: 50%; }}
        .s-meta .right {{ text-align: right; width: 50%; }}
        /* CANCELLED: centred directly below the shop details, before customer / invoice details. */
        .s-cancelled-row {{ text-align: center; margin: -8px 0 14px 0; }}
        .s-cancelled {{
            font-size: {c("size.cancelled")}pt; font-weight: 800; letter-spacing: 1px;
            text-transform: uppercase; color: {c("color.cancelled")};
        }}
        .s-heading {{
            font-weight: 600; text-transform: uppercase; color: {c("color.text_primary")}; margin-bottom: 3px;
        }}
        .s-muted {{ color: {c("color.text_secondary")}; }}
        .s-strong {{ font-weight: 600; color: {c("color.text_primary")}; }}

        .s-items {{ width: 100%; margin: 4px 0 18px 0; }}
        .s-items thead {{ display: table-header-group; }}
        .s-items tr {{ page-break-inside: avoid; break-inside: avoid; }}
        .s-items th {{
            border-top: 1px solid {c("color.table_border")};
            border-bottom: 1.5px solid {c("color.primary")};
            padding: 8px 6px; text-align: left; font-weight: 600; color: {c("color.text_primary")};
        }}
        .s-items td {{ border-bottom: 1px solid {c("color.table_border")}; padding: 12px 6px; vertical-align: top; }}
        {column_widths}
        .s-items .center {{ text-align: center; }}
        .s-items .right {{ text-align: right; }}
        .s-item-media {{ border: none; }}
        .s-item-media td {{ border: none; padding: 0; vertical-align: middle; }}
        .s-item-img {{ width: 34px; height: 34px; object-fit: cover; margin-right: 8px; }}
        .s-item-spec {{ font-size: {c("size.product_spec")}pt; color: #000000; margin-top: 3px; }}

        .s-summary {{ width: 100%; margin-top: 16px; page-break-inside: avoid; break-inside: avoid; }}
        .s-summary > tbody > tr > td {{ vertical-align: top; padding: 0; border: none; }}
        .s-details-line {{ line-height: 1.6; }}
        .s-totals {{ width: 100%; }}
        .s-totals td {{ padding: 4px 0; border: none; }}
        .s-totals .val {{ text-align: right; white-space: nowrap; }}
        .s-totals .grand td {{
            font-size: {c("size.grand_total")}pt; font-weight: 800; padding: 8px 0; color: {c("color.primary")};
        }}
        .s-totals .grand.has-note td {{ padding-bottom: 0; }}
        .s-totals .gap td {{ padding-top: 10px; }}
        .s-totals .divider td {{ border-top: 1px solid {c("color.divider")}; padding: 0; height: 6px; }}
        .s-totals .balance td {{ font-weight: 600; }}
        .s-totals .note td {{ font-size: 7.5pt; color: {c("color.text_secondary")}; text-align: right; padding-top: 0; }}

        .s-terms {{ page-break-inside: avoid; break-inside: avoid; }}
        .s-terms-title {{ font-weight: 600; text-transform: uppercase; color: {c("color.primary")}; margin-bottom: 4px; }}
        .s-terms-list {{ color: {c("color.text_secondary")}; line-height: 1.55; }}

        .s-watermark {{
            position: fixed; bottom: 14px; left: 76px; right: 76px; text-align: right;
            border-top: 1px solid {c("color.divider")}; padding-top: 6px;
            font-size: {c("size.watermark")}pt; color: {c("color.text_secondary")};
        }}

        .s-measurements {{ margin-top: 24px; }}
        .s-measurements-title {{
            font-size: {c("size.shop_name")}pt; font-weight: 800; text-align: center;
            color: {c("color.text_primary")}; margin-bottom: 10px;
        }}
        .s-measure-pair {{ width: 100%; margin-bottom: 14px; page-break-inside: avoid; break-inside: avoid; }}
        .s-measure-pair > tbody > tr > td {{ width: 50%; vertical-align: top; padding: 0 6px; border: none; }}
        .s-measure-card {{ page-break-inside: avoid; break-inside: avoid; margin-bottom: 14px; }}
        /* Key-value cards and per-person tables share one style at 80% size; widths are inline. */
        .s-measure-table {{ width: 100%; table-layout: fixed; font-size: {scaled(c("size.body"))}pt; }}
        .s-measure-table th, .s-measure-table td {{
            border-bottom: 1px solid {c("color.table_border")}; padding: {scaled(6)}px {scaled(7)}px; text-align: left;
            word-wrap: break-word; overflow-wrap: break-word;
        }}
        .s-measure-table th {{ font-weight: 600; border-bottom: 1.5px solid {c("color.primary")}; }}
        .s-measure-table .right {{ text-align: right; }}
        .s-measure-table th.measurement-name, .s-measure-table th.size-col,
        .s-measure-table th.person-col {{ color: {c("color.text_primary")}; }}
        /* Per-person cards start at the same left edge as the key-value cards (grid cell inset). */
        .s-measurements > .s-measure-card {{ margin-left: 6px; }}
        """

    # ---- data shaping ----------------------------------------------------
    def detail_lines(self, context):
        """Plain shop detail lines (no icons), in the reference order."""
        shop = context.shop
        address = ", ".join(p for p in (shop.address, shop.pincode) if p)
        phones = ", ".join(p for p in (shop.phone, shop.phone2) if p)
        gst = f"GST : {shop.gst_number}" if shop.gst_number and self.cfg("show.gst") else ""
        return [line for line in (address, shop.city, phones, shop.email, gst) if line]

    def totals(self, context):
        # CANCELLED is shown below the shop details instead of in the totals;
        # the separator sits below Balance Due (same rule as Classic).
        context = replace(context, totals=divider_below_balance(context.totals))
        return [entry for entry in super().totals(context) if entry["row"].kind != ROW_CANCELLED]

    def template_context(self, context) -> dict:
        return {
            **super().template_context(context),
            "detail_lines": self.detail_lines(context),
            "show_cancelled": context.is_cancelled,
            "cancelled_label": self.cfg("label.cancelled"),
            "watermark": self.cfg("label.watermark"),
        }
