"""
Booking List PDF (ReportLab).

Same look as the ledger book PDF: it reuses ledger_report_pdf's colors,
fonts, cached paragraph styles, page-number canvas and logo/currency helpers
unchanged. Layout: shop header -> title + applied filters -> summary ->
bookings table (header row repeats on every page).

All user-entered text (shop details, client names, product names, search
term) is XML-escaped before it reaches ReportLab's Paragraph markup.
"""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..ledger_report_pdf import LedgerBookPDFService
from ..ledger_report_pdf import constants
from ..ledger_report_pdf import fonts  # noqa: F401  (registers the report fonts)
from ..ledger_report_pdf.canvas import PageNumberCanvas
from ..ledger_report_pdf.constants import (
    BLACK, DARK_GREY, HEADER_BG_COLOR, SUMMARY_BG_COLOR, SUMMARY_BORDER_COLOR,
    TABLE_HEADER_BG, TABLE_HEADER_TEXT, TABLE_ROW_ALT_COLOR, TABLE_ROW_MAIN_COLOR,
)
from ..ledger_report_pdf.images import get_placeholder
from ..ledger_report_pdf.styles import get_cached_style

TITLE = "Booking List"
NO_BOOKINGS_TEXT = "No bookings found for the selected filters"
COLUMNS = (
    # (header, style, width fraction)
    ("S.No.", "table_header_sm", 0.07),
    ("Pickup Date", "table_header_sm", 0.14),
    ("Invoice ID", "table_header_sm", 0.12),
    ("Products", "table_header_left_sm", 0.25),
    ("Client", "table_header_left_sm", 0.20),
    ("Paid", "table_header_right_sm", 0.11),
    ("Pending", "table_header_right_sm", 0.11),
)


def _text(value) -> str:
    return escape(str(value)) if value not in (None, "") else ""


class _Divider(Flowable):
    """Thin header divider, same as the ledger PDF."""

    def __init__(self, width, thickness=0.5):
        super().__init__()
        self.width = width
        self.thickness = thickness

    def wrap(self, avail_width, avail_height):
        return self.width, self.thickness

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(HEADER_BG_COLOR)
        canvas.setLineWidth(self.thickness)
        canvas.setStrokeAlpha(0.3)
        canvas.line(0, self.thickness / 2, self.width, self.thickness / 2)
        canvas.restoreState()


class BookingListPDFService:

    @staticmethod
    def _header(shop, shop_info, width):
        logo = LedgerBookPDFService.get_shop_logo_safe(shop, 50) if shop else get_placeholder(50, 50, is_logo=True)
        lines = [Paragraph(_text(shop_info.get("name")) or "Shop", get_cached_style("shop_name"))]
        if shop_info.get("address"):
            lines.append(Paragraph(_text(shop_info["address"]), get_cached_style("shop_address")))
        location = ", ".join(p for p in (shop_info.get("city"), shop_info.get("state")) if p)
        if shop_info.get("pincode"):
            location = f"{location} - {shop_info['pincode']}" if location else shop_info["pincode"]
        if location:
            lines.append(Paragraph(_text(location), get_cached_style("shop_address")))
        for label, key in (("Phone", "phone"), ("Phone 2", "phone2"), ("Email", "email"), ("GST", "gst_number")):
            if shop_info.get(key):
                lines.append(Paragraph(f"{label}: {_text(shop_info[key])}", get_cached_style("shop_contact")))

        table = Table([[logo, lines]], colWidths=[60, width - 60])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 15),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return table

    @staticmethod
    def _summary(totals, width):
        fmt = LedgerBookPDFService.format_currency
        rows = [
            [Paragraph("Total Bookings", get_cached_style("summary_label")),
             Paragraph(str(totals["count"]), get_cached_style("summary_value"))],
            [Paragraph("Total Paid", get_cached_style("summary_label")),
             Paragraph(fmt(totals["paid"]), get_cached_style("summary_value"))],
            [Paragraph("Total Pending", get_cached_style("summary_label_bold")),
             Paragraph(fmt(totals["pending"]), get_cached_style("summary_value_bold"))],
        ]
        table = Table(rows, colWidths=[width * 0.6, width * 0.4])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -2), SUMMARY_BG_COLOR),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E5D8EF")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 15),
            ("RIGHTPADDING", (0, 0), (-1, -1), 15),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("BOX", (0, 0), (-1, -1), 1, SUMMARY_BORDER_COLOR),
            ("LINEBELOW", (0, 0), (-1, -2), 1, SUMMARY_BORDER_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, -1), BLACK),
        ]))
        return table

    @staticmethod
    def _bookings_table(rows, width):
        fmt = LedgerBookPDFService.format_currency
        cell = get_cached_style("table_cell_sm")
        center = get_cached_style("table_cell_center_sm")
        right = get_cached_style("table_cell_right_sm")

        data = [[Paragraph(header, get_cached_style(style)) for header, style, _ in COLUMNS]]
        for row in rows:
            products = "<br/>".join(f"&#8226; {_text(name)}" for name in row["products"]) or "-"
            client = f"<b>{_text(row['client_name']) or '-'}</b>"
            if row["client_phone"]:
                client += f"<br/>{_text(row['client_phone'])}"
            data.append([
                Paragraph(str(row["sno"]), center),
                Paragraph(_text(row["pickup"]) or "-", center),
                Paragraph(_text(row["invoice_id"]) or "-", center),
                Paragraph(products, cell),
                Paragraph(client, cell),
                Paragraph(fmt(row["paid"]), right),
                Paragraph(fmt(row["pending"]), right),
            ])

        table = Table(data, colWidths=[width * fraction for _, _, fraction in COLUMNS], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
            ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
            ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
            ("TOPPADDING", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
            ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (0, -1), 4),
            ("RIGHTPADDING", (0, 0), (0, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [TABLE_ROW_MAIN_COLOR, TABLE_ROW_ALT_COLOR]),
            ("LINEBELOW", (0, 0), (-1, 0), 1, TABLE_HEADER_BG),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
            ("LINEBEFORE", (0, 0), (0, -1), 1, colors.HexColor("#D1D5DB")),
            ("LINEAFTER", (-1, 0), (-1, -1), 1, colors.HexColor("#D1D5DB")),
        ]))
        return table

    @staticmethod
    def generate(shop_info, rows, totals, filter_lines, shop=None) -> bytes:
        """
        shop_info: {name, address, city, state, pincode, phone, phone2, email, gst_number}
        rows:      [{sno, pickup, invoice_id, products: [str], client_name, client_phone, paid, pending}]
        totals:    {count, paid, pending}
        filter_lines: plain-text lines shown under the title (escaped here)
        """
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=60)
        width = doc.width

        elements = [
            BookingListPDFService._header(shop, shop_info, width),
            Spacer(1, 10),
            _Divider(width),
            Spacer(1, 18),
            Paragraph(TITLE, get_cached_style("title")),
        ]
        for line in filter_lines:
            elements.append(Paragraph(_text(line), get_cached_style("shop_contact")))
        elements += [
            Spacer(1, 16),
            Paragraph("Summary", get_cached_style("section_title")),
            BookingListPDFService._summary(totals, width),
            Spacer(1, 22),
            Paragraph("Bookings", get_cached_style("section_title")),
            Spacer(1, 6),
        ]
        if rows:
            elements.append(BookingListPDFService._bookings_table(rows, width))
        else:
            elements.append(Paragraph(NO_BOOKINGS_TEXT, ParagraphStyle(
                "NoBookings", parent=getSampleStyleSheet()["Normal"], fontName=constants.BASE_FONT,
                fontSize=12, leading=16, textColor=DARK_GREY, alignment=TA_CENTER,
            )))

        doc.build(elements, canvasmaker=PageNumberCanvas)
        return buffer.getvalue()
