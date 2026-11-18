from io import BytesIO
from typing import Any, Dict, Iterable

from django.utils.timezone import localtime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Flowable, Image,
)

from ..ledger_report_pdf import constants
from ..ledger_report_pdf.constants import (
    logger,
    HEADER_BG_COLOR, TABLE_HEADER_BG, TABLE_HEADER_TEXT,
    TABLE_ROW_ALT_COLOR, TABLE_ROW_MAIN_COLOR, DARK_GREY,
)
from ..ledger_report_pdf.images import get_placeholder, download_image_optimized, _resize_image
from ..ledger_report_pdf.styles import get_cached_style
from ..ledger_report_pdf.canvas import PageNumberCanvas
from ..ledger_report_pdf import fonts  # noqa: F401 - ensure fonts are registered


class ClientDetailsPDFService:
    """PDF generation service for a shop's client list (S.No. / Name /
    Phone Number 1 / Phone Number 2)."""

    @staticmethod
    def get_shop_logo_safe(shop, size: int = 50) -> Flowable:
        """Same logic as LedgerBookPDFService.get_shop_logo_safe / other
        services' copies - kept local to avoid a cross-service dependency
        on any one PDF service (see account_history_pdf/service.py)."""
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
    def generate_client_details_pdf(
        shop_info: Dict[str, Any],
        clients: Iterable[Any],
        shop=None,
    ) -> bytes:
        """
        Build a PDF listing the shop's clients: shop header, a "Client
        Details" heading, and a table of S.No. / Name / Phone Number 1 /
        Phone Number 2.

        `clients` is any iterable of objects/rows exposing `client_name`,
        `phone_1_e164`, `phone_2_e164` (a queryset - ideally already
        `.only(...)`-restricted and `.iterator()`-backed by the caller for
        large shops - or a plain list both work, since this function only
        ever iterates it once).
        """
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=60,
        )
        elements = []
        available_width = doc.width

        # ===== HEADER: shop logo + details =====
        shop_logo = ClientDetailsPDFService.get_shop_logo_safe(shop, 50)
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

        # ===== TITLE / HEADING =====
        elements.append(Paragraph("Client Details", get_cached_style('title')))
        elements.append(Spacer(1, 15))

        # ===== CLIENTS TABLE =====
        header_row = [
            Paragraph("S.No.", get_cached_style('table_header')),
            Paragraph("Date", get_cached_style('table_header')),
            Paragraph("Name", get_cached_style('table_header_left')),
            Paragraph("Phone Number 1", get_cached_style('table_header')),
            Paragraph("Phone Number 2", get_cached_style('table_header')),
        ]
        col_widths = [
            available_width * 0.09,
            available_width * 0.15,
            available_width * 0.32,
            available_width * 0.22,
            available_width * 0.22,
        ]
        table_data = [header_row]
        row_count = 0
        for index, client in enumerate(clients, start=1):
            row_count += 1
            phone_1 = str(client.phone_1_e164) if getattr(client, 'phone_1_e164', None) else '-'
            phone_2 = str(client.phone_2_e164) if getattr(client, 'phone_2_e164', None) else '-'
            # Client created date in local time (IST), dd/mm/yyyy.
            created_at = getattr(client, 'created_at', None)
            date_text = localtime(created_at).strftime('%d/%m/%Y') if created_at else '-'
            table_data.append([
                Paragraph(str(index), get_cached_style('table_cell_center')),
                Paragraph(date_text, get_cached_style('table_cell_center')),
                Paragraph(str(client.client_name or ''), get_cached_style('table_cell')),
                Paragraph(phone_1, get_cached_style('table_cell_center')),
                Paragraph(phone_2, get_cached_style('table_cell_center')),
            ])

        if row_count:
            clients_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            clients_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                ("LEFTPADDING", (0, 0), (-1, 0), 8),
                ("RIGHTPADDING", (0, 0), (-1, 0), 8),
                ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("TOPPADDING", (0, 1), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
                ("LEFTPADDING", (0, 1), (-1, -1), 8),
                ("RIGHTPADDING", (0, 1), (-1, -1), 8),
                ("ALIGN", (0, 1), (1, -1), "CENTER"),   # S.No., Date
                ("ALIGN", (2, 1), (2, -1), "LEFT"),     # Name
                ("ALIGN", (3, 1), (4, -1), "CENTER"),   # Phone Number 1, 2
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [TABLE_ROW_MAIN_COLOR, TABLE_ROW_ALT_COLOR]),
                ("LINEBELOW", (0, 0), (-1, 0), 1, TABLE_HEADER_BG),
                ("LINEBEFORE", (0, 0), (0, -1), 1, TABLE_HEADER_BG),
                ("LINEAFTER", (-1, 0), (-1, -1), 1, TABLE_HEADER_BG),
            ]))
            elements.append(clients_table)
        else:
            no_data_style = ParagraphStyle(
                'NoClients', parent=getSampleStyleSheet()['Normal'],
                fontName=constants.BASE_FONT, fontSize=12, leading=16,
                textColor=DARK_GREY, alignment=TA_CENTER,
            )
            elements.append(Paragraph("No clients found for this shop", no_data_style))

        try:
            doc.build(elements, canvasmaker=PageNumberCanvas)
        except Exception as e:
            logger.error(f"Client details PDF generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate PDF: {e}")

        buffer.seek(0)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
