"""
Booking invoice data -> InvoiceContext.

Mirrors the data logic of BookingPDFService.generate_transaction_pdf
(whatsapp_integration/v3/pdf_template.py) so the numbers on a customized
invoice are exactly the numbers on the existing one.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum

from invoice_templates.models import DocumentType
from whatsapp_integration.v3.pdf_template import BasePDFService, BookingPDFService

from .base import (
    ROW_BALANCE, ROW_CANCELLED, ROW_DIVIDER, ROW_GRAND, ROW_LINE, ROW_NOTE, ROW_PAID,
    ROW_SUBTOTAL, Column, InvoiceContext, InvoiceItem, MeasurementData, PartyInfo, TotalRow,
)
from .common import build_shop_info, charge_rows, measurement_rows, parse_info_lines, tax_rows

logger = logging.getLogger("api_requests")
S = BookingPDFService

COLUMNS = [
    Column("no", "NO", "40px", "center"),
    Column("item", "ITEM"),
    Column("qty", "QTY", "60px", "center"),
    Column("price", "PRICE", "80px", "right"),
    Column("amount", "AMOUNT", "100px", "right"),
]


def _unit_price(amount, quantity) -> Decimal:
    """Price per unit = amount / quantity (not stored); the amount itself when quantity is unusable."""
    try:
        amount = Decimal(str(amount or 0))
    except (ValueError, InvalidOperation):
        return Decimal(0)
    try:
        quantity = Decimal(str(quantity))
    except (ValueError, InvalidOperation):
        return amount
    return amount / quantity if quantity > 0 else amount


def _item_image_url(item):
    # Fallback priority: item.product_thumbnail -> product.thumbnail_image -> product.image
    img_url = S.safe_get(item, "product_thumbnail")
    if not img_url:
        try:
            if item.product_variant and item.product_variant.product:
                product = item.product_variant.product
                img_url = product.thumbnail_image or product.image
        except Exception:
            pass
    return img_url or None


def _product_code_prefix(shop) -> str:
    # One query for the whole invoice (never per item); no "PRD" fallback.
    from shop.models import ShopNumberingSequence

    shop_id = S.safe_get(shop, "id")
    if not shop_id:
        return ""
    return ShopNumberingSequence.objects.filter(shop_id=shop_id).values_list(
        "product_code_prefix", flat=True
    ).first() or ""


def _refund_totals(booking_id):
    """(security_refund, security_deduction, charges_refund) in 2 queries."""
    from booking_v2.models import RefundDetails, SecurityDepositAdjustment

    security = SecurityDepositAdjustment.objects.filter(booking_id=booking_id).aggregate(
        refund=Sum("amount", filter=Q(adjustment_type="refund")),
        deduction=Sum("amount", filter=Q(adjustment_type="deduction")),
    )
    charges_refund = RefundDetails.objects.filter(booking_id=booking_id).aggregate(
        total=Sum("refunded_amount")
    )["total"]
    return (
        Decimal(str(security["refund"] or 0)),
        Decimal(str(security["deduction"] or 0)),
        Decimal(str(charges_refund or 0)),
    )


def _measurements(items, invoice_no, pickup_date, client_name, client_phone, staff_name) -> MeasurementData:
    data = MeasurementData(
        invoice_no=str(invoice_no or ""),
        pickup_str=S.format_date(pickup_date) if pickup_date else "",
        client_name=client_name,
        client_phone=client_phone,
        staff_name=staff_name,
    )
    for item in items:
        try:
            variant = getattr(item, "product_variant", None)
            product = getattr(variant, "product", None) if variant else None
            shop_service = getattr(product, "shop_service", None) if product else None
            general_service = getattr(shop_service, "general_service", None) if shop_service else None
            main_cat = getattr(general_service, "main_category", None) if general_service else None
            main_name = (getattr(main_cat, "name", "") or "").lower()
            if "dress" not in main_name:
                continue

            measurements = getattr(item, "measurements", None)
            person_entries = []
            legacy_measurements = measurements
            if S._is_person_measurements(measurements):
                person_entries = [m for m in measurements if S._is_person_measurements([m])]
                legacy_measurements = [m for m in measurements if not S._is_person_measurements([m])]
            rows = measurement_rows(legacy_measurements) if legacy_measurements else []
            if not rows and not person_entries:
                continue

            product_name = S.safe_get(product, "name", "") if product else ""
            if not product_name:
                product_name = S.safe_get(item, "product_name", "Unknown Product")
            variant_attr = S.safe_get(variant, "attribute", "") if variant else ""
            if not variant_attr:
                variant_attr = S.safe_get(item, "variant_attribute", "")
            heading = f"{product_name} - {variant_attr}" if variant_attr else str(product_name)

            if rows:
                data.sections.append((heading, rows))
            if person_entries:
                columns, person_rows = S._build_person_measurement_table(person_entries)
                if person_rows:
                    data.person_sections.append((heading, columns, person_rows))
        except Exception as e:
            logger.warning(f"Error processing measurements section for item: {e}")
    return data


def build_booking_context(shop, client, transaction, items, main_service_name="", terms=None) -> InvoiceContext:
    """`items` must be a list (callers already materialize the prefetch)."""
    items = list(items)
    S.validate_data(shop, client, transaction, items)

    shop_info = build_shop_info(shop)
    product_code_prefix = _product_code_prefix(shop)

    invoice_no = S.safe_get(transaction, "shop_sale_id") or S.safe_get(transaction, "shop_booking_id", "N/A")
    invoice_date = S.safe_get(transaction, "created_at") or S.safe_get(transaction, "date")
    staff_name = S.get_staff_name(transaction)

    client_name = str(S.safe_get(client, "client_name", ""))
    client_phone = str(S.safe_get(client, "phone_1_e164", "") or S.safe_get(client, "client_phone_1", ""))
    client_address = str(S.safe_get(transaction, "client_address") or S.safe_get(client, "address", ""))

    image_urls = [url for url in (_item_image_url(item) for item in items) if url]
    image_results = S.download_images_parallel(image_urls) if image_urls else {}

    invoice_items = []
    for idx, item in enumerate(items, 1):
        name, details = S.build_item_details(item)
        img_url = _item_image_url(item)
        code_line = ""
        try:
            if item.product_variant and item.product_variant.product:
                raw_code = item.product_variant.product.product_code
                if raw_code:
                    code_line = f"{product_code_prefix}{raw_code}"
        except Exception:
            pass
        invoice_items.append(InvoiceItem(
            no=idx,
            name=str(name),
            code_line=code_line,
            details=[str(d) for d in details],
            details_inline=True,
            quantity=S.safe_get(item, "quantity", 0),
            price=S.format_currency(_unit_price(S.safe_get(item, "amount", 0), S.safe_get(item, "quantity", 0))),
            amount=S.format_currency(S.safe_get(item, "amount", 0)),
            image=image_results.get(img_url) if img_url else None,
            image_url_present=bool(img_url),
        ))

    try:
        total = Decimal(str(S.safe_get(transaction, "total_amount", 0)))
        security = Decimal(str(S.safe_get(transaction, "security_amount", 0)))
        discount = Decimal(str(S.safe_get(transaction, "discount_amount", 0)))
        paid = Decimal(str(S.safe_get(transaction, "advance_amount", 0)))
        is_tax_applied = bool(S.safe_get(transaction, "is_tax_applied", False))
        tax_name = S.safe_get(transaction, "tax_name", "") or "GST"
        tax_rate = Decimal(str(S.safe_get(transaction, "tax_rate", 0)))
        tax_amount = Decimal(str(S.safe_get(transaction, "tax_amount", 0)))
        tax_calculation_type = S.safe_get(transaction, "tax_calculation_type", "") or ""
        is_inclusive_tax = is_tax_applied and tax_calculation_type == "inclusive"
    except (ValueError, InvalidOperation) as e:
        logger.error(f"Failed to parse decimal values: {e}")
        raise ValueError(f"Invalid decimal values in transaction: {e}")

    additional_total = Decimal(0)
    additional_rows = []
    charges = S.safe_get(transaction, "additional_charges")
    if charges:
        try:
            additional_total, additional_rows = charge_rows(charges.all())
        except Exception as e:
            logger.warning(f"Failed to calculate additional charges: {e}")
            additional_total, additional_rows = Decimal(0), []

    security_amount_is_paid = S._resolve_security_amount_is_paid(transaction)
    grand_total = (total + security) - discount
    sub_total = total - additional_total - (Decimal(0) if is_inclusive_tax else tax_amount)
    collected_security = security if security_amount_is_paid else Decimal(0)
    paid_with_security = paid + collected_security
    total_payable = (grand_total - security) + security
    is_cancelled = str(S.safe_get(transaction, "delivery_status", "")).strip().lower() == "cancelled"

    security_refund = security_deduction = charges_refund = Decimal(0)
    try:
        booking_id = S.safe_get(transaction, "id")
        if booking_id:
            security_refund, security_deduction, charges_refund = _refund_totals(booking_id)
    except Exception as e:
        logger.warning(f"Failed to calculate refund details for booking invoice: {e}")

    balance = max(total_payable - paid_with_security, Decimal(0))
    total_refunded = charges_refund + security_refund
    is_room_booking = "room" in (main_service_name or "").lower()
    fmt = S.format_currency

    totals = [TotalRow(
        ROW_SUBTOTAL,
        "room_price" if is_room_booking else "product_cost",
        "ROOM PRICE" if is_room_booking else "PRODUCT COST",
        fmt(sub_total),
    )]
    totals += additional_rows
    if discount > 0:
        totals.append(TotalRow(ROW_LINE, "discount", "DISCOUNT", f"-{fmt(discount)}"))
    if security > 0:
        totals.append(TotalRow(ROW_LINE, "security", "SECURITY", fmt(security), section_gap=True))
    totals += tax_rows(tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax)
    totals.append(TotalRow(ROW_GRAND, "grand_total", "TOTAL PAYABLE", fmt(total_payable)))
    if is_inclusive_tax:
        totals.append(TotalRow(ROW_NOTE, "inclusive_tax_note", "(Inclusive of all taxes.)"))
    totals.append(TotalRow(ROW_DIVIDER, "divider"))
    totals.append(TotalRow(ROW_PAID, "paid", "PAID AMOUNT", fmt(paid_with_security)))
    if is_cancelled:
        totals.append(TotalRow(ROW_CANCELLED, "cancelled", "BOOKING CANCELLED"))
        if total_refunded > 0:
            totals.append(TotalRow(ROW_LINE, "refunded", "REFUNDED", f"-{fmt(total_refunded)}"))
    else:
        totals.append(TotalRow(ROW_BALANCE, "balance", "BALANCE DUE", fmt(balance)))
        if security > 0 and security_amount_is_paid:
            totals.append(TotalRow(ROW_DIVIDER, "divider"))
            totals.append(TotalRow(ROW_LINE, "security_collected", "SECURITY COLLECTED", fmt(collected_security)))
            if security_deduction > 0:
                totals.append(TotalRow(ROW_LINE, "security_deduction", "DEDUCTION", fmt(security_deduction)))
            totals.append(TotalRow(ROW_LINE, "security_refunded", "REFUNDED", fmt(security_refund)))

    try:
        measurements = _measurements(
            items, invoice_no, S.safe_get(transaction, "pickup_date"), client_name, client_phone, staff_name,
        )
    except Exception as e:
        logger.error(f"Failed building measurements page: {e}", exc_info=True)
        measurements = MeasurementData()

    return InvoiceContext(
        document_type=DocumentType.BOOKING,
        shop=shop_info,
        party=PartyInfo(name=client_name, phone=client_phone, address=client_address),
        invoice_no=str(invoice_no),
        invoice_date=S.format_datetime(invoice_date),
        staff_name=staff_name,
        columns=COLUMNS,
        items=invoice_items,
        info_key="booking_info",
        info_title="BOOKING INFO:",
        info_lines=parse_info_lines(S.get_booking_info(transaction, main_service_name=main_service_name)),
        totals=totals,
        terms=list(terms) if terms is not None else [],
        is_cancelled=is_cancelled,
        is_inclusive_tax=is_inclusive_tax,
        is_room_booking=is_room_booking,
        measurements=measurements,
        filename=BasePDFService.build_invoice_filename(
            client_name or "Customer", S.safe_get(transaction, "shop_booking_id") or S.safe_get(transaction, "id")
        ),
    )
