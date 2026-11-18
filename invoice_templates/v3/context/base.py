"""
Template-agnostic invoice data.

Context builders (booking.py / sale.py / tailor.py / sample.py) turn real or
sample data into an InvoiceContext; renderers only ever read an
InvoiceContext. All values are plain, unescaped text - renderers escape them
(Django template autoescape / html.escape) so user-entered data can never
inject markup into the PDF.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Total row kinds (visual role, mapped to CSS by each renderer)
ROW_SUBTOTAL = "subtotal"      # first summary row (e.g. PRODUCT COST / SUB TOTAL)
ROW_LINE = "line"              # ordinary line (charges, discount, tax, security...)
ROW_GRAND = "grand"            # TOTAL PAYABLE / GRAND TOTAL
ROW_NOTE = "note"              # "(Inclusive of all taxes.)"
ROW_DIVIDER = "divider"        # thin separator between groups
ROW_PAID = "paid"              # PAID AMOUNT
ROW_BALANCE = "balance"        # BALANCE DUE
ROW_CANCELLED = "cancelled"    # BOOKING / ORDER CANCELLED banner


@dataclass
class ShopInfo:
    name: str = ""
    address: str = ""
    city: str = ""
    pincode: str = ""
    phone: str = ""
    phone2: str = ""
    email: str = ""
    gst_number: str = ""
    logo: Optional[str] = None            # data: URI (already downloaded) or None
    detail_lines: List[str] = field(default_factory=list)  # legacy header order


@dataclass
class PartyInfo:
    name: str = ""
    phone: str = ""
    address: str = ""
    show_name: bool = True                # Sale invoices never print the name


@dataclass
class Column:
    key: str                               # no | item | price | qty | amount
    default_label: str                     # legacy (Standard) heading
    width: Optional[str] = None            # e.g. "40px"
    align: str = "left"                    # left | center | right


@dataclass
class InvoiceItem:
    no: int
    name: str
    code_line: str = ""
    details: List[str] = field(default_factory=list)
    details_inline: bool = False           # Booking joins details on one line
    quantity: object = 0
    price: str = ""                        # Sale (stored) / Booking (amount / qty)
    amount: str = ""
    image: Optional[str] = None            # data: URI or None
    image_url_present: bool = False


@dataclass
class InfoLine:
    label: str
    value: str
    bold_label: bool = False


@dataclass
class TotalRow:
    kind: str
    key: str                               # semantic key (product_cost, discount, ...)
    label: str = ""                        # legacy (Standard) label
    value: str = ""
    section_gap: bool = False
    dynamic: bool = False                  # label comes from data (charge / tax name)
    raw_label: str = ""                    # data label in its original casing


@dataclass
class MeasurementData:
    # legacy two-column cards: [(heading, [{'name', 'value'}])]
    sections: List[Tuple[str, list]] = field(default_factory=list)
    # per-person tables: [(heading, columns, rows)]
    person_sections: List[Tuple[str, list, list]] = field(default_factory=list)
    invoice_no: str = ""
    pickup_str: str = ""
    client_name: str = ""
    client_phone: str = ""
    staff_name: str = ""

    @property
    def has_content(self) -> bool:
        return bool(self.sections or self.person_sections)


@dataclass
class InvoiceContext:
    document_type: str
    shop: ShopInfo
    party: PartyInfo
    invoice_no: str
    invoice_date: str
    staff_name: str
    columns: List[Column]
    items: List[InvoiceItem]
    info_key: str                          # booking_info | sale_info | work_info
    info_title: str                        # legacy title, e.g. "BOOKING INFO:"
    info_lines: List[InfoLine]
    totals: List[TotalRow]
    terms: List[str]
    is_cancelled: bool = False
    is_inclusive_tax: bool = False
    is_room_booking: bool = False
    measurements: MeasurementData = field(default_factory=MeasurementData)
    filename: str = ""
