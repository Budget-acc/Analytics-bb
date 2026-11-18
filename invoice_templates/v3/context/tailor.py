"""
Custom Work / Tailor Order invoice data -> InvoiceContext.

Mirrors the data logic of TailorOrderPDFService.generate_tailor_order_pdf
(whatsapp_integration/v3/pdf_template.py).
"""
import logging
from decimal import Decimal, InvalidOperation

from django.db.models import Sum

from invoice_templates.models import DocumentType
from whatsapp_integration.v3.pdf_template import BasePDFService, TailorOrderPDFService

from .base import (
    ROW_BALANCE, ROW_CANCELLED, ROW_DIVIDER, ROW_GRAND, ROW_LINE, ROW_NOTE, ROW_PAID,
    Column, InfoLine, InvoiceContext, InvoiceItem, MeasurementData, PartyInfo, TotalRow,
)
from .common import build_shop_info, charge_rows, measurement_rows, tax_rows

logger = logging.getLogger("api_requests")
S = TailorOrderPDFService

COLUMNS = [
    Column("no", "NO", "40px", "center"),
    Column("item", "ITEM"),
    Column("qty", "QTY", "60px", "center"),
    Column("amount", "AMOUNT", "100px", "right"),
]


def _item_image_url(item):
    # Fallback priority: item.reference_image -> product.thumbnail_image -> product.image
    img_url = S.safe_get(item, "reference_image", None)
    if not img_url:
        try:
            pv = item.created_product_variant
            if pv and pv.product:
                img_url = pv.product.thumbnail_image or pv.product.image
        except Exception:
            pass
    return img_url or None


def _refund_totals(order_id):
    from tailored.models import TailorOrderRefund, TailorOrderSecurityDepositAdjustment

    security_refund = TailorOrderSecurityDepositAdjustment.objects.filter(
        order_id=order_id, adjustment_type="refunded"
    ).aggregate(total=Sum("amount"))["total"]
    charges_refund = TailorOrderRefund.objects.filter(order_id=order_id).aggregate(
        total=Sum("refunded_amount")
    )["total"]
    return Decimal(str(security_refund or 0)), Decimal(str(charges_refund or 0))


def build_tailor_context(shop, client, tailor_order, items, terms=None) -> InvoiceContext:
    items = list(items)
    S.validate_data(shop, tailor_order, items)

    shop_info = build_shop_info(shop)
    invoice_no = S.safe_get(tailor_order, "shop_tailor_id") or "N/A"
    staff_name = S.get_staff_name(tailor_order)

    client_name = str(S.safe_get(client, "client_name", "") if client else "")
    client_phone = str(
        (S.safe_get(client, "phone_1_e164", "") if client else "")
        or S.safe_get(tailor_order, "client_phone_number", "")
    )

    image_urls = [url for url in (_item_image_url(item) for item in items) if url]
    image_results = S.download_images_parallel(image_urls) if image_urls else {}

    invoice_items = []
    for idx, item in enumerate(items, 1):
        product_type = str(S.safe_get(item, "product_type", ""))
        img_url = _item_image_url(item)
        invoice_items.append(InvoiceItem(
            no=idx,
            name=str(S.safe_get(item, "product_name", "Unknown Product")),
            details=[product_type] if product_type else [],
            quantity=S.safe_get(item, "quantity", 0),
            amount=S.format_currency(S.safe_get(item, "customer_item_charge", 0)),
            image=image_results.get(img_url) if img_url else None,
            image_url_present=bool(img_url),
        ))

    try:
        product_total = sum(
            Decimal(str(S.safe_get(item, "customer_item_charge", 0) or 0)) for item in items
        )
        additional_total = Decimal(0)
        additional_rows = []
        charges = S.safe_get(tailor_order, "additional_charges")
        if charges:
            try:
                charge_list = list(charges.all()) if hasattr(charges, "all") else list(charges)
                additional_total, additional_rows = charge_rows(charge_list)
            except Exception as e:
                logger.warning(f"Failed to calculate additional charges for tailor order: {e}")
                additional_rows = []
                additional_total = Decimal(str(S.safe_get(tailor_order, "total_additional_charges", 0) or 0))
        else:
            additional_total = Decimal(str(S.safe_get(tailor_order, "total_additional_charges", 0) or 0))
        discount = Decimal(str(S.safe_get(tailor_order, "discount_amount", 0) or 0))
        paid = Decimal(str(S.safe_get(tailor_order, "paid_amount", 0) or 0))
        security = Decimal(str(S.safe_get(tailor_order, "security_amount", 0) or 0))
        is_tax_applied = bool(S.safe_get(tailor_order, "is_tax_applied", False))
        tax_name = S.safe_get(tailor_order, "tax_name", "") or "GST"
        tax_rate = Decimal(str(S.safe_get(tailor_order, "tax_rate", 0)))
        tax_amount = Decimal(str(S.safe_get(tailor_order, "tax_amount", 0)))
        tax_calculation_type = S.safe_get(tailor_order, "tax_calculation_type", "") or ""
        is_inclusive_tax = is_tax_applied and tax_calculation_type == "inclusive"
    except (ValueError, InvalidOperation) as e:
        logger.error(f"Failed to parse decimal values for tailor order: {e}")
        raise ValueError(f"Invalid decimal values in tailor order: {e}")

    grand_total = product_total + additional_total + security - discount + (
        Decimal("0") if is_inclusive_tax else tax_amount
    )
    security_is_paid = S._resolve_security_is_paid(tailor_order)
    collected_security = security if security_is_paid else Decimal("0")
    paid_with_security = paid + collected_security
    is_cancelled = str(getattr(tailor_order, "status", "")).strip().lower() == "cancelled"

    security_refund = charges_refund = Decimal("0")
    try:
        order_id = tailor_order.id
        if order_id:
            security_refund, charges_refund = _refund_totals(order_id)
    except Exception as e:
        logger.warning(f"Failed to calculate refund details for tailor order invoice: {e}")

    total_refunded = charges_refund + security_refund
    balance = max(grand_total - paid_with_security, Decimal("0"))
    fmt = S.format_currency

    # The existing tailor invoice styles PRODUCT TOTAL as an ordinary line.
    totals = [TotalRow(ROW_LINE, "product_total", "PRODUCT TOTAL", fmt(product_total))]
    totals += additional_rows
    if discount > 0:
        totals.append(TotalRow(ROW_LINE, "discount", "DISCOUNT", f"-{fmt(discount)}"))
    if security > 0:
        totals.append(TotalRow(ROW_LINE, "security", "SECURITY", fmt(security), section_gap=True))
    totals += tax_rows(tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax)
    totals.append(TotalRow(ROW_GRAND, "grand_total", "TOTAL PAYABLE", fmt(grand_total)))
    if is_inclusive_tax:
        totals.append(TotalRow(ROW_NOTE, "inclusive_tax_note", "(Inclusive of all taxes.)"))
    totals.append(TotalRow(ROW_DIVIDER, "divider"))
    totals.append(TotalRow(ROW_PAID, "paid", "PAID AMOUNT", fmt(paid_with_security)))
    if is_cancelled:
        totals.append(TotalRow(ROW_CANCELLED, "cancelled", "ORDER CANCELLED"))
        if total_refunded > 0:
            totals.append(TotalRow(ROW_LINE, "refunded", "REFUNDED", f"-{fmt(total_refunded)}"))
    else:
        totals.append(TotalRow(ROW_BALANCE, "balance", "BALANCE DUE", fmt(balance)))
        if security > 0:
            totals.append(TotalRow(ROW_DIVIDER, "divider"))
            totals.append(TotalRow(ROW_LINE, "security_collected", "SECURITY COLLECTED", fmt(collected_security)))
            totals.append(TotalRow(ROW_LINE, "security_refunded", "SECURITY REFUNDED", fmt(security_refund)))

    order_type = S.safe_get(tailor_order, "customer_order_type", "")
    pickup_date = S.safe_get(tailor_order, "pickup_date")
    return_date = S.safe_get(tailor_order, "return_date")
    info_lines = []
    if pickup_date:
        info_lines.append(InfoLine("Pickup", S.format_date(pickup_date)))
    if order_type == "rental" and return_date:
        info_lines.append(InfoLine("Return", S.format_date(return_date)))

    measurements = MeasurementData(
        invoice_no=str(invoice_no or ""),
        pickup_str=S.format_date(pickup_date) if pickup_date else "",
        client_name=client_name,
        client_phone=client_phone,
        staff_name=staff_name,
    )
    try:
        for item in items:
            rows = measurement_rows(getattr(item, "measurements", None))
            if not rows:
                continue
            heading = str(S.safe_get(item, "product_name", "Unknown Product"))
            product_type = S.safe_get(item, "product_type", "")
            if product_type:
                heading += f" - {product_type}"
            measurements.sections.append((heading, rows))
    except Exception as e:
        logger.error(f"Failed building tailor order measurements page: {e}", exc_info=True)
        measurements.sections = []

    return InvoiceContext(
        document_type=DocumentType.TAILOR_ORDER,
        shop=shop_info,
        party=PartyInfo(name=client_name, phone=client_phone),
        invoice_no=str(invoice_no),
        invoice_date=S.format_datetime(S.safe_get(tailor_order, "created_at")),
        staff_name=staff_name,
        columns=COLUMNS,
        items=invoice_items,
        info_key="work_info",
        info_title="WORK INFO:",
        info_lines=info_lines,
        totals=totals,
        terms=list(terms) if terms is not None else [],
        is_cancelled=is_cancelled,
        is_inclusive_tax=is_inclusive_tax,
        measurements=measurements,
        filename=BasePDFService.build_invoice_filename(
            client_name or "Customer", S.safe_get(tailor_order, "shop_tailor_id") or S.safe_get(tailor_order, "id")
        ),
    )
