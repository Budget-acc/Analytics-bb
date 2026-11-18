from io import BytesIO
from decimal import Decimal
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Flowable, Image,
)

from ERP.utils.number_format import format_indian_number, format_inr
from ..ledger_report_pdf import constants
from ..ledger_report_pdf.constants import (
    logger,
    HEADER_BG_COLOR, TABLE_HEADER_BG, TABLE_HEADER_TEXT,
    BLACK, DARK_GREY, CUSTOM_PURPLE,
    SUMMARY_BG_COLOR, SUMMARY_BORDER_COLOR,
)
from ..ledger_report_pdf.images import get_placeholder, download_image_optimized, _resize_image
from ..ledger_report_pdf.styles import get_cached_style
from ..ledger_report_pdf.canvas import PageNumberCanvas
from ..ledger_report_pdf import fonts  # noqa: F401 - ensure fonts are registered


class _RoundedCardWrapper(Flowable):
    """Wraps a single flowable (typically a Table) in a rounded-corner card:
    a filled, bordered rounded rectangle drawn behind the wrapped content.
    Copied from account_history_pdf/service.py (kept local per-service, same
    as that copy is kept local there, to avoid a cross-service dependency)."""

    def __init__(self, inner, radius=8, fill_color=None, border_color=None, border_width=1, padding=10, x_shift=0):
        super().__init__()
        self.inner = inner
        self.radius = radius
        self.fill_color = fill_color
        self.border_color = border_color
        self.border_width = border_width
        self.padding = padding
        self.x_shift = x_shift
        self.width = 0
        self.height = 0

    def wrap(self, availWidth, availHeight):
        inner_width, inner_height = self.inner.wrap(
            availWidth - 2 * self.padding, availHeight - 2 * self.padding
        )
        self.width = inner_width + 2 * self.padding
        self.height = inner_height + 2 * self.padding
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        if self.x_shift:
            c.translate(-self.x_shift, 0)
        if self.fill_color is not None:
            c.setFillColor(self.fill_color)
        if self.border_color is not None:
            c.setStrokeColor(self.border_color)
            c.setLineWidth(self.border_width)
        c.roundRect(
            0, 0, self.width, self.height, self.radius,
            stroke=1 if self.border_color is not None else 0,
            fill=1 if self.fill_color is not None else 0,
        )
        c.restoreState()
        self.inner.drawOn(self.canv, self.padding - self.x_shift, self.padding)


_TAX_REPORT_STYLE_CACHE = {}


def _get_tax_report_style(name):
    """Styles specific to this PDF only - kept out of the shared
    ledger_report_pdf/styles.py cache so changes here never affect other
    reports that reuse those cached styles."""
    if name not in _TAX_REPORT_STYLE_CACHE:
        if name == 'grand_total_label':
            _TAX_REPORT_STYLE_CACHE[name] = ParagraphStyle(
                'TaxReportGrandTotalLabel',
                parent=get_cached_style('summary_label_bold'),
                fontSize=8,
                leading=10,
            )
        elif name == 'grand_total_value':
            _TAX_REPORT_STYLE_CACHE[name] = ParagraphStyle(
                'TaxReportGrandTotalValue',
                parent=get_cached_style('summary_value_bold'),
                fontSize=8,
                leading=10,
            )
        elif name == 'body_cell':
            # 1pt smaller than the Grand Total row, so no body cell (e.g. the
            # 10-char Date column) wraps to a second line.
            _TAX_REPORT_STYLE_CACHE[name] = ParagraphStyle(
                'TaxReportBodyCell',
                parent=get_cached_style('table_cell'),
                fontSize=7,
                leading=9,
            )
        elif name == 'body_cell_right':
            _TAX_REPORT_STYLE_CACHE[name] = ParagraphStyle(
                'TaxReportBodyCellRight',
                parent=get_cached_style('table_cell_right'),
                fontSize=7,
                leading=9,
            )
    return _TAX_REPORT_STYLE_CACHE[name]


_TYPE_LABELS = {
    'bookings': 'Booking',
    'sales': 'Sale',
    'tailor_orders': 'Custom Work',
}


class TaxReportPDFService:
    """PDF generation service for the consolidated Tax Report."""

    @staticmethod
    def format_currency(amount) -> str:
        try:
            if amount is None:
                return "-"
            if not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            return format_inr(amount, decimals=2)
        except Exception:
            return "₹0.00"

    @staticmethod
    def format_amount_plain(amount) -> str:
        try:
            if amount is None:
                return "-"
            if not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            return format_indian_number(amount, decimals=2)
        except Exception:
            return "0.00"

    @staticmethod
    def get_shop_logo_safe(shop, size: int = 50) -> Flowable:
        """Same logic as AccountHistoryPDFService.get_shop_logo_safe - kept
        local to avoid a cross-service dependency."""
        try:
            logo_url = None
            if shop is not None:
                if hasattr(shop, 'logo') and shop.logo:
                    logo_url = shop.logo if isinstance(shop.logo, str) else getattr(shop.logo, 'url', None)
                if not logo_url and hasattr(shop, 'img') and shop.img:
                    logo_url = shop.img if isinstance(shop.img, str) else getattr(shop.img, 'url', None)

            if logo_url:
                raw_bytes = download_image_optimized(logo_url, timeout=3, retries=1)
                if raw_bytes:
                    resized = _resize_image(raw_bytes, size=(size, size), maintain_aspect=True)
                    if resized:
                        try:
                            return Image(resized, width=size, height=size)
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Failed to get shop logo: {e}")

        return get_placeholder(size, size, is_logo=True)

    @staticmethod
    def generate_tax_report_pdf(
        shop_info: Dict[str, Any],
        rows: List[Dict[str, Any]],
        summary: Dict[str, Decimal],
        start_date: str,
        end_date: str,
        shop=None,
    ) -> bytes:
        """
        Build the consolidated Tax Report PDF: shop header (incl. GSTIN),
        statement period, an overall summary card (taxable amount, tax,
        CGST, SGST), and a flat, date-descending transaction table styled
        consistently with the Account Statement report.

        `rows` is the flat list of tax report rows (already date-descending,
        as produced by build_tax_report_queryset), each with reference_id,
        date, order_id, type, taxable_amount, tax_rate, tax_amount, cgst, sgst.
        `summary` is the dict returned by get_tax_report_summary.
        """
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=60,
        )
        elements = []
        available_width = doc.width

        # ===== HEADER: shop logo + details (incl. GSTIN) =====
        shop_logo = TaxReportPDFService.get_shop_logo_safe(shop, 50)
        shop_name = shop_info.get('name', '')
        shop_name_para = Paragraph(shop_name, get_cached_style('shop_name'))

        details_lines = []
        address = shop_info.get('address', '')
        if address:
            details_lines.append(Paragraph(address, get_cached_style('shop_address')))
        phone = shop_info.get('phone', '')
        if phone:
            details_lines.append(Paragraph(f"Phone: {phone}", get_cached_style('shop_contact')))
        phone2 = shop_info.get('phone2', '')
        if phone2:
            details_lines.append(Paragraph(f"Phone 2: {phone2}", get_cached_style('shop_contact')))
        email = shop_info.get('email', '')
        if email:
            details_lines.append(Paragraph(f"Email: {email}", get_cached_style('shop_contact')))
        gst_number = shop_info.get('gst_number', '')
        if gst_number:
            details_lines.append(Paragraph(f"GSTIN: {gst_number}", get_cached_style('shop_contact')))

        header_table = Table(
            [[shop_logo, [shop_name_para] + details_lines]],
            colWidths=[60, available_width - 60],
        )
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 15),
            ("LEFTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 10))

        class LineFlowable(Flowable):
            def __init__(self, width, color, thickness):
                super().__init__()
                self.width = width
                self.color = color
                self.thickness = thickness

            def wrap(self, availWidth, availHeight):
                return (self.width, self.thickness)

            def draw(self):
                c = self.canv
                c.saveState()
                c.setStrokeColor(HEADER_BG_COLOR)
                c.setLineWidth(self.thickness)
                c.setStrokeAlpha(0.3)
                c.line(0, self.thickness / 2, self.width, self.thickness / 2)
                c.restoreState()

        elements.append(LineFlowable(available_width, HEADER_BG_COLOR, 0.5))
        elements.append(Spacer(1, 18))

        # ===== TITLE =====
        elements.append(Paragraph("Tax Report", get_cached_style('title')))

        # ===== OVERALL SUMMARY (key-value card) =====
        summary_rows = [
            ("Statement From", start_date or "-"),
            ("Statement To", end_date or "-"),
            ("Total Taxable Amount", TaxReportPDFService.format_currency(summary.get('total_taxable_amount'))),
            ("Total Tax Amount", TaxReportPDFService.format_currency(summary.get('total_tax_amount'))),
            ("Total CGST", TaxReportPDFService.format_currency(summary.get('total_cgst'))),
            ("Total SGST", TaxReportPDFService.format_currency(summary.get('total_sgst'))),
        ]
        summary_table_data = [
            [
                Paragraph(label, get_cached_style('summary_label')),
                Paragraph(":", get_cached_style('summary_label')),
                Paragraph(str(value), get_cached_style('table_cell')),
            ]
            for label, value in summary_rows
        ]
        card_padding = 6
        card_left_shift = 4
        card_width_reduction = 3
        card_content_width = available_width - 2 * card_padding - card_width_reduction
        label_col_width = 150
        colon_col_width = 14
        summary_table = Table(
            summary_table_data,
            colWidths=[label_col_width, colon_col_width, card_content_width - label_col_width - colon_col_width],
        )
        summary_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 4),
            ("LEFTPADDING", (1, 0), (1, -1), 0),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ("LEFTPADDING", (2, 0), (2, -1), 4),
            ("RIGHTPADDING", (2, 0), (2, -1), 0),
            ("LINEBELOW", (0, 0), (-1, -2), 0.5, SUMMARY_BORDER_COLOR),
        ]))
        summary_card = _RoundedCardWrapper(
            summary_table,
            radius=8,
            fill_color=SUMMARY_BG_COLOR,
            border_color=SUMMARY_BORDER_COLOR,
            border_width=1,
            padding=card_padding,
            x_shift=card_left_shift,
        )
        elements.append(summary_card)
        elements.append(Spacer(1, 15))

        # ===== TRANSACTIONS TABLE =====
        elements.append(Paragraph("Transactions", get_cached_style('section_title')))

        if rows:
            header_row = [
                Paragraph("Date", get_cached_style('table_header')),
                Paragraph("Order ID", get_cached_style('table_header_left')),
                Paragraph("Type", get_cached_style('table_header_left')),
                Paragraph("Taxable Amount", get_cached_style('table_header_right')),
                Paragraph("Tax Rate", get_cached_style('table_header_right')),
                Paragraph("CGST", get_cached_style('table_header_right')),
                Paragraph("SGST", get_cached_style('table_header_right')),
                Paragraph("Tax Amount", get_cached_style('table_header_right')),
            ]
            col_widths = [
                available_width * 0.11,
                available_width * 0.15,
                available_width * 0.13,
                available_width * 0.15,
                available_width * 0.11,
                available_width * 0.115,
                available_width * 0.115,
                available_width * 0.14,
            ]
            table_data = [header_row]
            total_taxable = Decimal("0")
            total_tax = Decimal("0")
            total_cgst = Decimal("0")
            total_sgst = Decimal("0")
            for row in rows:
                taxable_amount = row.get('taxable_amount') or Decimal("0")
                tax_rate = row.get('tax_rate') or Decimal("0")
                tax_amount = row.get('tax_amount') or Decimal("0")
                if row.get('cgst') is not None and row.get('sgst') is not None:
                    cgst = row.get('cgst')
                    sgst = row.get('sgst')
                else:
                    # Round CGST first, then derive SGST as the remainder so
                    # the two always sum exactly to tax_amount.
                    cgst = (tax_amount / 2).quantize(Decimal("0.01"))
                    sgst = tax_amount - cgst

                total_taxable += taxable_amount
                total_tax += tax_amount
                total_cgst += cgst
                total_sgst += sgst

                row_date = row.get('date')
                date_text = row_date.strftime('%d/%m/%Y') if hasattr(row_date, 'strftime') else str(row_date or '')
                type_text = _TYPE_LABELS.get(row.get('type'), row.get('type') or '')

                table_data.append([
                    Paragraph(date_text, _get_tax_report_style('body_cell')),
                    Paragraph(str(row.get('order_id') or ''), _get_tax_report_style('body_cell')),
                    Paragraph(type_text, _get_tax_report_style('body_cell')),
                    Paragraph(TaxReportPDFService.format_currency(taxable_amount), _get_tax_report_style('body_cell_right')),
                    Paragraph(f"{tax_rate}%", _get_tax_report_style('body_cell_right')),
                    Paragraph(TaxReportPDFService.format_currency(cgst), _get_tax_report_style('body_cell_right')),
                    Paragraph(TaxReportPDFService.format_currency(sgst), _get_tax_report_style('body_cell_right')),
                    Paragraph(TaxReportPDFService.format_currency(tax_amount), _get_tax_report_style('body_cell_right')),
                ])

            grand_total_row_index = len(table_data)
            table_data.append([
                Paragraph("", _get_tax_report_style('body_cell')),
                Paragraph("Grand Total", _get_tax_report_style('grand_total_label')),
                Paragraph("", _get_tax_report_style('body_cell')),
                Paragraph(TaxReportPDFService.format_amount_plain(total_taxable), _get_tax_report_style('grand_total_value')),
                Paragraph("", _get_tax_report_style('body_cell_right')),
                Paragraph(TaxReportPDFService.format_amount_plain(total_cgst), _get_tax_report_style('grand_total_value')),
                Paragraph(TaxReportPDFService.format_amount_plain(total_sgst), _get_tax_report_style('grand_total_value')),
                Paragraph(TaxReportPDFService.format_amount_plain(total_tax), _get_tax_report_style('grand_total_value')),
            ])

            transactions_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            transactions_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (0, 1), (2, -1), "LEFT"),
                ("ALIGN", (3, 1), (7, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, TABLE_HEADER_BG),
                ("BOX", (0, 0), (-1, -1), 1, TABLE_HEADER_BG),
                ("LINEBELOW", (0, 0), (-1, 0), 1, TABLE_HEADER_BG),
                ("LINEAFTER", (0, 0), (0, 0), 0.25, colors.white),
                ("LINEAFTER", (1, 0), (1, 0), 0.25, colors.white),
                ("LINEAFTER", (2, 0), (2, 0), 0.25, colors.white),
                ("LINEAFTER", (3, 0), (3, 0), 0.25, colors.white),
                ("LINEAFTER", (4, 0), (4, 0), 0.25, colors.white),
                ("LINEAFTER", (5, 0), (5, 0), 0.25, colors.white),
                ("LINEAFTER", (6, 0), (6, 0), 0.25, colors.white),
                # Grand Total row - bold text on an accent background,
                # overriding the alternating rows above for this row.
                ("BACKGROUND", (0, grand_total_row_index), (-1, grand_total_row_index), colors.HexColor("#E5D8EF")),
                ("TEXTCOLOR", (0, grand_total_row_index), (-1, grand_total_row_index), CUSTOM_PURPLE),
                ("FONTNAME", (0, grand_total_row_index), (-1, grand_total_row_index), constants.BOLD_FONT),
                ("LINEABOVE", (0, grand_total_row_index), (-1, grand_total_row_index), 1, TABLE_HEADER_BG),
            ]))
            elements.append(transactions_table)
        else:
            no_data_style = ParagraphStyle(
                'NoData', parent=getSampleStyleSheet()['Normal'],
                fontName=constants.BASE_FONT, fontSize=12, leading=16,
                textColor=DARK_GREY, alignment=TA_CENTER,
            )
            elements.append(Paragraph("No transactions found for the selected period", no_data_style))

        try:
            doc.build(elements, canvasmaker=PageNumberCanvas)
        except Exception as e:
            logger.error(f"Tax report PDF generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate PDF: {e}")

        buffer.seek(0)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
