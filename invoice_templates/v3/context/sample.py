"""
Static sample data for one-page template previews.

The shop header (name, address, phones, logo) and the shop's resolved Terms &
Conditions are real so the preview shows what the shop's invoices will look
like; everything transactional (customer, items, totals) is fixed sample
data.

The sample records are in-memory stand-ins shaped like the ORM objects and go
through the same context builders as real invoices, so totals, the CGST/SGST
breakdown and the CANCELLED row/label are exactly what a real PDF shows. The
booking and tailor order samples are cancelled (sales have no cancelled
state). The only database access is the booking builder's product-code
prefix lookup; sample ids are None, so no refund queries run.
"""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace as NS

from django.utils import timezone

from invoice_templates.models import DocumentType

from .booking import build_booking_context
from .sale import build_sale_context
from .tailor import build_tailor_context


class _Related(list):
    """Minimal related-manager stand-in (.all() / .first() / .order_by())."""

    def all(self):
        return self

    def first(self):
        return self[0] if self else None

    def order_by(self, *args):
        return self


_CREATED_AT = timezone.make_aware(dt.datetime(2025, 6, 16, 8, 22))
_CLIENT = NS(client_name="Anjali Menon", phone_1_e164="+91 98765 43210", client_phone_1="", address="MG Road, Kochi")
_STAFF = NS(name="Ramees")
# Exclusive GST, so the breakdown prints as CGST + SGST rows (inclusive GST prints none).
_GST = dict(is_tax_applied=True, tax_name="GST", tax_rate=Decimal("5"), tax_calculation_type="exclusive")
_NO_IMAGE = dict(thumbnail_image=None, image=None)


def _booking_item(name, code, color, category, quantity, amount):
    product = NS(name=name, product_code=code, shop_service=None, **_NO_IMAGE)
    return NS(
        product_name=name, category=category, model="", color=color, variant_attribute="",
        quantity=quantity, amount=Decimal(amount), product_thumbnail=None, measurements=None,
        product_variant=NS(attribute="", product=product),
    )


def _booking(shop, terms):
    items = [
        _booking_item("Bridal Necklace Set", "00012", "Gold", "Temple", 1, "1500"),
        _booking_item("Kundan Earrings", "00027", "Red", "Kundan", 2, "1550"),
    ]
    booking = NS(
        id=None, shop_booking_id="000381", shop_sale_id=None, created_at=_CREATED_AT,
        # total_amount = items 3,050.00 + exclusive GST 152.50
        total_amount=Decimal("3202.50"), tax_amount=Decimal("152.50"), **_GST,
        security_amount=Decimal("1000"), security_amount_is_paid=True,
        discount_amount=Decimal("200"), advance_amount=Decimal("2000"),
        additional_charges=_Related(), delivery_status="cancelled",
        pickup_date=_CREATED_AT, pickup_time=dt.time(8, 22),
        return_date=_CREATED_AT + dt.timedelta(days=2), return_time=None,
        details={"location_from": "Main Showroom"}, staff_id=1, staff=_STAFF, client_address="",
    )
    return build_booking_context(shop, _CLIENT, booking, items, terms=terms)


def _sale_item(name, details, price, quantity):
    color, category = details
    product = NS(name=name, category=category, model="", color=color, shop_service=None, **_NO_IMAGE)
    return NS(
        product_variant=NS(attribute="", product=product),
        price=Decimal(price), quantity=quantity, subtotal=Decimal(price) * quantity,
    )


def _sale(shop, terms):
    items = [
        _sale_item("Silk Saree", ("Maroon", "Kanchipuram"), "4500", 1),
        _sale_item("Designer Blouse", ("Gold", "Blouse"), "1000", 2),
    ]
    sale = NS(
        shop_sale_id="SAL-000127", created_at=_CREATED_AT, sale_date=_CREATED_AT.date(),
        client_phone_e164=_CLIENT.phone_1_e164, address=_CLIENT.address,
        # total_amount = items 6,500.00 + exclusive GST 310.00
        total_amount=Decimal("6810"), tax_amount=Decimal("310"), **_GST,
        discount=Decimal("300"), paid_amount=Decimal("5000"), additional_charges=_Related(),
        payments=_Related([NS(get_payment_method_display=lambda: "UPI")]), staff_id=1, staff=_STAFF,
    )
    return build_sale_context(shop, _CLIENT, sale, items, terms=terms)


def _tailor_item(name, product_type, quantity, charge):
    return NS(
        product_name=name, product_type=product_type, quantity=quantity,
        customer_item_charge=Decimal(charge), reference_image=None, created_product_variant=None,
        measurements=None,
    )


def _tailor(shop, terms):
    items = [
        _tailor_item("Bridal Lehenga", "Stitching", 1, "8500"),
        _tailor_item("Blouse Alteration", "Alteration", 2, "600"),
    ]
    order = NS(
        id=None, shop_tailor_id="TLR-000045", created_at=_CREATED_AT, client_phone_number="",
        additional_charges=_Related([NS(name="Embroidery work", amount=Decimal("1200"))]),
        discount_amount=Decimal("300"), paid_amount=Decimal("6000"), security_amount=Decimal("0"),
        tax_amount=Decimal("500"), **_GST,
        security_payments=_Related(), status="cancelled",
        customer_order_type="stitching", pickup_date=dt.date(2025, 6, 25), return_date=None,
        staff_id=1, staff=_STAFF,
    )
    return build_tailor_context(shop, _CLIENT, order, items, terms=terms)


_BUILDERS = {
    DocumentType.BOOKING: _booking,
    DocumentType.SALE: _sale,
    DocumentType.TAILOR_ORDER: _tailor,
}


def build_sample_context(document_type, shop, terms):
    """Sample invoice for `shop` (real header) with the shop's resolved terms."""
    return _BUILDERS[document_type](shop, list(terms))
