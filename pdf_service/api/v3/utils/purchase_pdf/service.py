from io import BytesIO
from decimal import Decimal
from typing import Any, Dict, List

from django.utils.timezone import localtime

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Flowable, Image

from ERP.utils.number_format import format_inr
from ..ledger_report_pdf import constants
from ..ledger_report_pdf.constants import logger, HEADER_BG_COLOR, TABLE_HEADER_BG, TABLE_HEADER_TEXT
from ..ledger_report_pdf.images import get_placeholder, download_image_optimized, _resize_image
from ..ledger_report_pdf.styles import get_cached_style
from ..ledger_report_pdf.canvas import PageNumberCanvas
from ..ledger_report_pdf import fonts  # noqa: F401 - ensure fonts are registered


class PurchasePDFService:
    """PDF generation service for the purchase invoice/record document."""

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
    def format_datetime(dt) -> str:
        if not dt:
            return ""
        try:
            return localtime(dt).strftime("%d/%m/%Y %I:%M %p")
        except Exception:
            return str(dt)

    @staticmethod
    def get_shop_logo_safe(shop, size: int = 50) -> Flowable:
        try:
            logo_url = None
            if shop is not None:
                if getattr(shop, "logo", None):
                    logo_url = shop.logo if isinstance(shop.logo, str) else getattr(shop.logo, "url", None)
                if not logo_url and getattr(shop, "img", None):
                    logo_url = shop.img if isinstance(shop.img, str) else getattr(shop.img, "url", None)
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
    def generate_purchase_pdf(shop, vendor, purchase, items: List[Any], additional_charges: List[Any], payments: List[Any]) -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=60)
        elements = []
        available_width = doc.width

        # ===== HEADER =====
        shop_logo = PurchasePDFService.get_shop_logo_safe(shop, 50)
        details_lines = [Paragraph(shop.name or "", get_cached_style("shop_name"))]
        if getattr(shop, "address", None):
            details_lines.append(Paragraph(shop.address, get_cached_style("shop_address")))
        if getattr(shop, "phone", None):
            details_lines.append(Paragraph(f"Phone: {shop.phone}", get_cached_style("shop_contact")))

        header_table = Table([[shop_logo, details_lines]], colWidths=[60, available_width - 60])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 15),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 18))

        # ===== TITLE + METADATA =====
        elements.append(Paragraph("Purchase Record", get_cached_style("title")))
        meta_rows = [
            ("Purchase Code", purchase.purchase_code or "-"),
            ("Vendor", vendor.name if vendor else "-"),
            ("Invoice Number", purchase.invoice_number or "-"),
            ("Purchase Date", PurchasePDFService.format_datetime(purchase.purchase_date)),
            ("Grand Total", PurchasePDFService.format_currency(purchase.grand_total)),
            ("Paid Amount", PurchasePDFService.format_currency(purchase.paid_amount)),
            ("Outstanding Balance", PurchasePDFService.format_currency(purchase.outstanding_balance)),
        ]
        meta_table = Table(
            [[Paragraph(f"<b>{label}</b>", get_cached_style("summary_label")), Paragraph(str(value), get_cached_style("table_cell"))] for label, value in meta_rows],
            colWidths=[available_width * 0.35, available_width * 0.65],
        )
        meta_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 15))

        # ===== ITEMS TABLE =====
        if items:
            elements.append(Paragraph("Items", get_cached_style("section_title")))
            header_row = [
                Paragraph("Product", get_cached_style("table_header_left")),
                Paragraph("Variant", get_cached_style("table_header")),
                Paragraph("Qty", get_cached_style("table_header_right")),
                Paragraph("Unit Price", get_cached_style("table_header_right")),
                Paragraph("Line Total", get_cached_style("table_header_right")),
            ]
            col_widths = [available_width * 0.36, available_width * 0.20, available_width * 0.12, available_width * 0.16, available_width * 0.16]
            table_data = [header_row]
            for item in items:
                table_data.append([
                    Paragraph(str(item.product_name or ""), get_cached_style("table_cell")),
                    Paragraph(str(item.variant_other or "-"), get_cached_style("table_cell_center")),
                    Paragraph(str(item.quantity), get_cached_style("table_cell_right")),
                    Paragraph(PurchasePDFService.format_currency(item.purchase_amount), get_cached_style("table_cell_right")),
                    Paragraph(PurchasePDFService.format_currency(item.line_total), get_cached_style("table_cell_right")),
                ])
            items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            items_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
                ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
                ("GRID", (0, 0), (-1, -1), 0.25, TABLE_HEADER_BG),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            elements.append(items_table)
            elements.append(Spacer(1, 15))

        # ===== ADDITIONAL CHARGES =====
        if additional_charges:
            elements.append(Paragraph("Additional Charges", get_cached_style("section_title")))
            header_row = [
                Paragraph("Charge", get_cached_style("table_header_left")),
                Paragraph("Account", get_cached_style("table_header")),
                Paragraph("Amount", get_cached_style("table_header_right")),
            ]
            table_data = [header_row]
            for charge in additional_charges:
                table_data.append([
                    Paragraph(str(charge.charge_name), get_cached_style("table_cell")),
                    Paragraph(charge.account.name if charge.account_id else "-", get_cached_style("table_cell_center")),
                    Paragraph(PurchasePDFService.format_currency(charge.amount), get_cached_style("table_cell_right")),
                ])
            charges_table = Table(table_data, colWidths=[available_width * 0.5, available_width * 0.25, available_width * 0.25], repeatRows=1)
            charges_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
                ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
                ("GRID", (0, 0), (-1, -1), 0.25, TABLE_HEADER_BG),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            elements.append(charges_table)
            elements.append(Spacer(1, 15))

        # ===== PAYMENTS =====
        if payments:
            elements.append(Paragraph("Payments", get_cached_style("section_title")))
            header_row = [
                Paragraph("Date", get_cached_style("table_header")),
                Paragraph("Account", get_cached_style("table_header")),
                Paragraph("Amount", get_cached_style("table_header_right")),
            ]
            table_data = [header_row]
            for payment in payments:
                table_data.append([
                    Paragraph(PurchasePDFService.format_datetime(payment.created_at), get_cached_style("table_cell_center")),
                    Paragraph(payment.account.name if payment.account_id else "-", get_cached_style("table_cell_center")),
                    Paragraph(PurchasePDFService.format_currency(payment.amount), get_cached_style("table_cell_right")),
                ])
            payments_table = Table(table_data, colWidths=[available_width * 0.4, available_width * 0.3, available_width * 0.3], repeatRows=1)
            payments_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
                ("FONTNAME", (0, 0), (-1, 0), constants.BOLD_FONT),
                ("GRID", (0, 0), (-1, -1), 0.25, TABLE_HEADER_BG),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            elements.append(payments_table)

        try:
            doc.build(elements, canvasmaker=PageNumberCanvas)
        except Exception as e:
            logger.error(f"Purchase PDF generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate PDF: {e}")

        buffer.seek(0)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
