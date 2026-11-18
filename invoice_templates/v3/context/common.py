"""
Helpers shared by the booking / sale / tailor context builders.

They reuse the existing BasePDFService helpers so formatting (currency,
dates, shop header, booking info lines, measurements) stays identical to the
existing invoices.
"""
import re
from decimal import Decimal

from whatsapp_integration.v3.pdf_template import BasePDFService

from .base import ROW_LINE, InfoLine, ShopInfo, TotalRow

S = BasePDFService

_STRONG_LINE_RE = re.compile(r"^<strong>(.*?):</strong>\s?(.*)$", re.S)


def build_shop_info(shop, download_logo=True) -> ShopInfo:
    """Shop header data. The logo is downloaded once (data: URI)."""
    detail_html = S.build_shop_details_html(shop)
    return ShopInfo(
        name=str(S.safe_get(shop, "name", "")),
        address=str(S.safe_get(shop, "address", "")),
        city=str(S.safe_get(shop, "city", "")),
        pincode=str(S.safe_get(shop, "pincode", "")),
        phone=str(S.safe_get(shop, "phone", "")),
        phone2=str(S.safe_get(shop, "phone2", "")),
        email=str(S.safe_get(shop, "email", "")),
        gst_number=str(S.safe_get(shop, "gst_number", "")),
        logo=S.download_image(S.safe_get(shop, "img")) if download_logo else None,
        detail_lines=detail_html.split("<br/>") if detail_html else [],
    )


def parse_info_lines(lines) -> list:
    """
    Convert BookingPDFService.get_booking_info() strings into structured
    lines. That helper emits either "Label: value" or
    "<strong>Label:</strong> value"; parsing (instead of re-implementing it)
    keeps the booking info logic in one place while letting renderers escape
    the user-entered values.
    """
    parsed = []
    for line in lines or []:
        line = str(line)
        match = _STRONG_LINE_RE.match(line)
        if match:
            parsed.append(InfoLine(label=match.group(1), value=match.group(2), bold_label=True))
            continue
        label, sep, value = line.partition(": ")
        if sep:
            parsed.append(InfoLine(label=label, value=value))
        else:
            parsed.append(InfoLine(label="", value=line))
    return parsed


def tax_rows(tax_name, tax_rate, tax_amount, is_tax_applied, is_inclusive_tax) -> list:
    """
    Structured equivalent of BasePDFService.build_tax_rows_html (same rules:
    no row for inclusive / no tax; GST split evenly into CGST + SGST with
    SGST as the remainder; any other tax name as one combined row).
    """
    if not is_tax_applied or is_inclusive_tax:
        return []

    tax_name = (tax_name or "").strip()
    tax_rate = Decimal(str(tax_rate or 0))
    tax_amount = Decimal(str(tax_amount or 0))

    if tax_name.upper() == "GST":
        half_rate = tax_rate / 2
        cgst_amount = (tax_amount / 2).quantize(Decimal("0.01"))
        sgst_amount = tax_amount - cgst_amount
        return [
            TotalRow(ROW_LINE, "tax", f"CGST ({float(half_rate):g}%)", S.format_currency(cgst_amount), dynamic=True),
            TotalRow(ROW_LINE, "tax", f"SGST ({float(half_rate):g}%)", S.format_currency(sgst_amount), dynamic=True),
        ]

    return [
        TotalRow(ROW_LINE, "tax", f"{tax_name.upper()} ({float(tax_rate):g}%)", S.format_currency(tax_amount), dynamic=True),
    ]


def charge_rows(charges) -> tuple:
    """(total, [TotalRow]) for additional charges, matching the existing invoices."""
    total = Decimal(0)
    rows = []
    for charge in charges:
        amount = Decimal(str(S.safe_get(charge, "amount", 0)))
        total += amount
        name = str(S.safe_get(charge, "name", "Additional Charge"))
        rows.append(TotalRow(
            ROW_LINE, "additional_charge", name.upper(), S.format_currency(amount),
            dynamic=True, raw_label=name,
        ))
    return total, rows


def measurement_rows(measurements) -> list:
    """Legacy measurement format (dict or list of {name, value}) -> rows."""
    rows = []
    if isinstance(measurements, dict):
        for key, value in measurements.items():
            if key is not None and value not in (None, ""):
                rows.append({"name": str(key), "value": str(value)})
    elif isinstance(measurements, list):
        for entry in measurements:
            if isinstance(entry, dict):
                name = entry.get("name")
                value = entry.get("value")
                if name and value not in (None, ""):
                    rows.append({"name": str(name), "value": str(value)})
    return rows
