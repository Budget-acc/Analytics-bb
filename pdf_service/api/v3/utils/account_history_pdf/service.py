from io import BytesIO
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.utils.timezone import localtime

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


_ACCOUNT_HISTORY_STYLE_CACHE = {}


def _get_account_history_style(name):
    """Styles specific to this PDF only - kept out of the shared
    ledger_report_pdf/styles.py cache so changes here never affect the
    ledger book report, which reuses those cached styles."""
    if name not in _ACCOUNT_HISTORY_STYLE_CACHE:
        if name == 'grand_total_label':
            _ACCOUNT_HISTORY_STYLE_CACHE[name] = ParagraphStyle(
                'AccountHistoryGrandTotalLabel',
                parent=get_cached_style('summary_label_bold'),
                fontSize=11,  # shared summary_label_bold is 14pt - too large here
                leading=14,
            )
        elif name == 'grand_total_value':
            _ACCOUNT_HISTORY_STYLE_CACHE[name] = ParagraphStyle(
                'AccountHistoryGrandTotalValue',
                parent=get_cached_style('summary_value_bold'),
                fontSize=11,
                leading=14,
            )
    return _ACCOUNT_HISTORY_STYLE_CACHE[name]


class _RoundedCardWrapper(Flowable):
    """Wraps a single flowable (typically a Table) in a rounded-corner card:
    a filled, bordered rounded rectangle drawn behind the wrapped content.
    ReportLab's Table/TableStyle only supports square BOX borders, so the
    rounded corners are drawn manually via canvas.roundRect."""

    def __init__(self, inner, radius=8, fill_color=None, border_color=None, border_width=1, padding=10, x_shift=0):
        super().__init__()
        self.inner = inner
        self.radius = radius
        self.fill_color = fill_color
        self.border_color = border_color
        self.border_width = border_width
        self.padding = padding
        # Cosmetic horizontal nudge (points) - positive shifts the whole
        # card left. Combine with a smaller colWidths total on `inner` (see
        # generate_account_history_pdf's card_width_reduction) to also pull
        # the right border in, so the card doesn't overflow past the
        # transactions table's right edge after shifting left.
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


class AccountHistoryPDFService:
    """PDF generation service for a single account's bank-statement-style history."""

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
        """Same numeric formatting as format_currency (comma separators,
        2 decimal places) but without the currency symbol - used for the
        Grand Total row's Debit/Credit values."""
        try:
            if amount is None:
                return "-"
            if not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            return format_indian_number(amount, decimals=2)
        except Exception:
            return "0.00"

    @staticmethod
    def format_datetime_12h(dt) -> str:
        """DD/MM/YYYY hh:mm AM/PM in local timezone."""
        if not dt:
            return ""
        try:
            return localtime(dt).strftime("%d/%m/%Y %I:%M %p")
        except Exception as e:
            logger.warning(f"Failed to format datetime {dt}: {e}")
            return str(dt)

    @staticmethod
    def get_shop_logo_safe(shop, size: int = 50) -> Flowable:
        """Same logic as LedgerBookPDFService.get_shop_logo_safe - kept local
        to avoid a cross-service dependency on LedgerBookPDFService itself."""
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
    def generate_account_history_pdf(
        shop_info: Dict[str, Any],
        account,
        entries: List[Any],
        opening_balance: Decimal,
        available_balance: Decimal,
        start_date: str,
        end_date: str,
        shop=None,
    ) -> bytes:
        """
        Build a bank-statement-style PDF for one account: shop header,
        account metadata, statement period, and a chronological (ascending)
        transaction table with Date & Time | Description | Debit | Credit |
        Balance columns.

        `entries` are LedgerEntry instances that already have `.balance_after`
        (running balance, set by the caller) and `.description` (set by
        transaction_description_builder.build_transaction_descriptions).
        """
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=60,
        )
        elements = []
        available_width = doc.width

        # ===== HEADER: shop logo + details =====
        shop_logo = AccountHistoryPDFService.get_shop_logo_safe(shop, 50)
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
        elements.append(Paragraph("Account Statement", get_cached_style('title')))

        # ===== ACCOUNT SUMMARY (key-value table, no section title) =====
        account_name = getattr(account, 'name', '') or ''
        summary_rows = [
            ("Account Name", account_name),
            ("Statement From", start_date),
            ("Statement To", end_date),
            ("Opening Balance", AccountHistoryPDFService.format_currency(opening_balance)),
            ("Available Balance", AccountHistoryPDFService.format_currency(available_balance)),
        ]
        summary_table_data = [
            [
                Paragraph(label, get_cached_style('summary_label')),
                Paragraph(":", get_cached_style('summary_label')),
                Paragraph(str(value), get_cached_style('table_cell')),
            ]
            for label, value in summary_rows
        ]
        # Card content width must account for the wrapper's own padding
        # (see _RoundedCardWrapper below) so the inner table + card border
        # together still fit within available_width.
        card_padding = 6
        # Manual fine-tune knobs: bump these up/down (points) if the card's
        # border ever drifts from the transactions table's border again.
        # card_left_shift moves the whole card left; card_width_reduction
        # shrinks its total width (pulls the right border in) so it doesn't
        # overflow past the table's right edge after shifting left.
        card_left_shift = 4
        card_width_reduction = 3
        card_content_width = available_width - 2 * card_padding - card_width_reduction
        label_col_width = 130
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
            # Explicit padding on every column - the default 6pt left+right
            # padding on the narrow colon column (14pt) left ~2pt usable
            # width in older attempts and could starve it to 0, which makes
            # reportlab's Paragraph wrap() fail catastrophically and throw
            # a "too large on page" LayoutError for the whole row.
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 4),
            ("LEFTPADDING", (1, 0), (1, -1), 0),
            ("RIGHTPADDING", (1, 0), (1, -1), 0),
            ("LEFTPADDING", (2, 0), (2, -1), 4),
            ("RIGHTPADDING", (2, 0), (2, -1), 0),
            # Row separator lines between key-value pairs, same accent color
            # family as the reference ledger report's summary table.
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

        if entries:
            header_row = [
                Paragraph("Date & Time", get_cached_style('table_header')),
                Paragraph("Description", get_cached_style('table_header_left')),
                Paragraph("Debit", get_cached_style('table_header_right')),
                Paragraph("Credit", get_cached_style('table_header_right')),
                Paragraph("Balance", get_cached_style('table_header_right')),
            ]
            col_widths = [
                available_width * 0.23,
                available_width * 0.35,
                available_width * 0.14,
                available_width * 0.14,
                available_width * 0.14,
            ]
            table_data = [header_row]
            total_debit = Decimal("0")
            total_credit = Decimal("0")
            for entry in entries:
                is_credit = entry.direction == 'in'
                if is_credit:
                    total_credit += entry.amount
                else:
                    total_debit += entry.amount
                debit_text = "-" if is_credit else AccountHistoryPDFService.format_currency(entry.amount)
                credit_text = AccountHistoryPDFService.format_currency(entry.amount) if is_credit else "-"
                table_data.append([
                    Paragraph(
                        AccountHistoryPDFService.format_datetime_12h(entry.created_at),
                        get_cached_style('table_cell'),
                    ),
                    Paragraph(str(getattr(entry, 'description', '') or ''), get_cached_style('table_cell')),
                    Paragraph(debit_text, get_cached_style('table_cell_right')),
                    Paragraph(credit_text, get_cached_style('table_cell_right')),
                    Paragraph(
                        AccountHistoryPDFService.format_currency(entry.balance_after),
                        get_cached_style('table_cell_right'),
                    ),
                ])

            grand_total_row_index = len(table_data)  # index of the row appended below
            table_data.append([
                Paragraph("", get_cached_style('table_cell')),
                Paragraph("Grand Total", _get_account_history_style('grand_total_label')),
                Paragraph(AccountHistoryPDFService.format_amount_plain(total_debit), _get_account_history_style('grand_total_value')),
                Paragraph(AccountHistoryPDFService.format_amount_plain(total_credit), _get_account_history_style('grand_total_value')),
                Paragraph("", get_cached_style('table_cell_right')),
            ])

            transactions_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            transactions_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTSIZE", (0, 1), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (0, 1), (0, -1), "LEFT"),
                ("ALIGN", (1, 1), (1, -1), "LEFT"),
                ("ALIGN", (2, 1), (4, -1), "RIGHT"),
                # Full grid - column and row separator lines, so the table
                # reads as a table rather than banded text blocks. Same
                # color as the table header background, per design.
                ("GRID", (0, 0), (-1, -1), 0.25, TABLE_HEADER_BG),
                ("BOX", (0, 0), (-1, -1), 1, TABLE_HEADER_BG),
                ("LINEBELOW", (0, 0), (-1, 0), 1, TABLE_HEADER_BG),
                # Header column separators - the GRID above draws these in
                # TABLE_HEADER_BG, same as the header's own background, so
                # they're invisible there. Redraw just the header row's
                # vertical dividers in white so the 5 columns (Date & Time /
                # Description / Debit / Credit / Balance) are visibly split.
                # repeatRows=1 means this applies to the header on every page.
                ("LINEAFTER", (0, 0), (0, 0), 0.25, colors.white),
                ("LINEAFTER", (1, 0), (1, 0), 0.25, colors.white),
                ("LINEAFTER", (2, 0), (2, 0), 0.25, colors.white),
                ("LINEAFTER", (3, 0), (3, 0), 0.25, colors.white),
                # Grand Total row - bold text on an accent background,
                # overriding the alternating ROWBACKGROUNDS above for this row.
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
            logger.error(f"Account history PDF generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate PDF: {e}")

        buffer.seek(0)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
