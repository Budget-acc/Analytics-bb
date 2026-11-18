"""
Sale invoice data -> InvoiceContext.

Mirrors the data logic of SalesPDFService.generate_sale_pdf
(whatsapp_integration/v3/pdf_template.py).
"""
import logging
from decimal import Decimal, InvalidOperation

from invoice_templates.models import DocumentType
from whatsapp_integration.v3.pdf_template import BasePDFService, SalesPDFService

from .base import (
    ROW_BALANCE, ROW_GRAND, ROW_LINE, ROW_NOTE, ROW_PAID, ROW_SUBTOTAL,
    Column, InfoLine, InvoiceContext, InvoiceItem, PartyInfo, TotalRow,
)
from .common import build_shop_info, charge_rows, tax_rows

logger = logging.getLogger("api_requests")
S = SalesPDFService

COLUMNS = [
    Column("no", "NO", "40px", "center"),
    Column("item", "ITEM"),
    Column("price", "PRICE", "80px", "right"),
    Column("qty", "QTY", "60px", "center"),
    Column("amount", "TOTAL", "100px", "right"),
]


def _item_image_url(item):
    try:
        if hasattr(item, "product_variant") and item.product_variant:
            product = item.product_variant.product
            if product:
                return product.thumbnail_image or product.image or None
    except AttributeError:
        pass
    return None


def _payment_method(sale) -> str:
    payment_method = "Cash"
    try:
        payments = S.safe_get(sale, "payments")
        if payments and hasattr(payments, "first") and payments.first():
            payment = payments.first()
            payment_method = S.safe_get(payment, "get_payment_method_display", lambda: "Cash")()
    except Exception as e:
        logger.warning(f"Failed to get payment method: {e}")
    return str(payment_method)


def build_sale_context(shop, client, sale, items, terms=None) -> InvoiceContext:
    items = list(items)
    S.validate_data(shop, client, sale, items)

    shop_info = build_shop_info(shop)
    invoice_no = S.safe_get(sale, "shop_sale_id", "N/A")
    staff_name = S.get_staff_name(sale)

    client_phone = str(S.safe_get(sale, "client_phone_e164", "") or S.safe_get(sale, "client_phone", ""))
    client_address = str(
        S.safe_get(sale, "address") or (S.safe_get(client, "address", "") if client else "")
    )

    image_urls = [url for url in (_item_image_url(item) for item in items) if url]
    image_results = S.download_images_parallel(image_urls) if image_urls else {}

    invoice_items = []
    for idx, item in enumerate(items, 1):
        name, details = S.build_item_details(item)
        img_url = _item_image_url(item)
        invoice_items.append(InvoiceItem(
            no=idx,
            name=str(name),
            details=[str(d) for d in details],
            quantity=S.safe_get(item, "quantity", 0),
            price=S.format_currency(S.safe_get(item, "price", 0)),
            amount=S.format_currency(S.safe_get(item, "subtotal", 0)),
            image=image_results.get(img_url) if img_url else None,
            image_url_present=bool(img_url),
        ))

    try:
        total = Decimal(str(S.safe_get(sale, "total_amount", 0)))
        discount = Decimal(str(S.safe_get(sale, "discount", 0)))
        paid = Decimal(str(S.safe_get(sale, "paid_amount", 0)))
        is_tax_applied = bool(S.safe_get(sale, "is_tax_applied", False))
        tax_name = S.safe_get(sale, "tax_name", "") or "GST"
        tax_rate = Decimal(str(S.safe_get(sale, "tax_rate", 0)))
        tax_amount = Decimal(str(S.safe_get(sale, "tax_amount", 0)))
        tax_calculation_type = S.safe_get(sale, "tax_calculation_type", "") or ""
        is_inclusive_tax = is_tax_applied and tax_calculation_type == "inclusive"
    except (ValueError, InvalidOperation) as e:
        logger.error(f"Failed to parse decimal values: {e}")
        raise ValueError(f"Invalid decimal values in sale: {e}")

    additional_total = Decimal(0)
    additional_rows = []
    charges = S.safe_get(sale, "additional_charges")
    if charges:
        try:
            additional_total, additional_rows = charge_rows(charges.all())
        except Exception as e:
            logger.warning(f"Failed to calculate additional charges: {e}")
            additional_total, additional_rows = Decimal(0), []

    grand_total = total - discount
    sub_total = total - additional_total - (Decimal(0) if is_inclusive_tax else tax_amount)
    balance = grand_total - paid
    fmt = S.format_currency

    totals = [TotalRow(ROW_SUBTOTAL, "sub_total", "SUB TOTAL", fmt(sub_total))]
    totals += additional_rows
    if discount > 0:
        # The existing sale invoice prints the discount without a minus sign.
        totals.append(TotalRow(ROW_LINE, "discount", "DISCOUNT", fmt(discount)))
    totals += tax_rows(tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax)
    totals.append(TotalRow(ROW_GRAND, "grand_total", "GRAND TOTAL", fmt(grand_total)))
    if is_inclusive_tax:
        totals.append(TotalRow(ROW_NOTE, "inclusive_tax_note", "(Inclusive of all taxes.)"))
    totals.append(TotalRow(ROW_PAID, "paid", "PAID AMOUNT", fmt(paid)))
    if balance > 0:
        totals.append(TotalRow(ROW_BALANCE, "balance", "BALANCE DUE", fmt(balance)))

    client_name = str(S.safe_get(client, "client_name", "") if client else "")
    return InvoiceContext(
        document_type=DocumentType.SALE,
        shop=shop_info,
        # The existing sale invoice shows phone + address only (no name).
        party=PartyInfo(name=client_name, phone=client_phone, address=client_address, show_name=False),
        invoice_no=str(invoice_no),
        invoice_date=S.format_datetime(S.safe_get(sale, "created_at")),
        staff_name=staff_name,
        columns=COLUMNS,
        items=invoice_items,
        info_key="sale_info",
        info_title="SALE INFO:",
        info_lines=[
            InfoLine("Date", S.format_date(S.safe_get(sale, "sale_date"))),
            InfoLine("Payment Method", _payment_method(sale)),
        ],
        totals=totals,
        terms=list(terms) if terms is not None else [],
        is_inclusive_tax=is_inclusive_tax,
        filename=BasePDFService.build_invoice_filename(client_name or "Customer", invoice_no),
    )
