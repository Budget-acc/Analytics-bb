"""
In-memory stand-ins for Shop / Client / Booking / Sale / TailorOrder and their
items, shaped like the ORM objects the invoice generators read. They let the
rendering tests run without a database.
"""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import mock

from django.utils import timezone


class Rel(list):
    """Minimal related-manager stand-in (.all() / .first() / .order_by())."""

    def all(self):
        return self

    def first(self):
        return self[0] if self else None

    def order_by(self, *args):
        return self


NOW = timezone.make_aware(dt.datetime(2025, 6, 16, 8, 22))


def make_shop(**overrides):
    data = dict(
        # No markup characters here: the existing invoice emits them unescaped,
        # the new renderer escapes them (covered by SecurityTests), so parity
        # comparisons use plain text.
        id=1, name="NW Bridal Premium Rental Jewellery and Costumes", address="Chamayam Tower OP",
        city="Kozhikode", pincode="679333", phone="+91 8848099399", phone2="", email="shop@example.com",
        gst_number="32ABCDE1234F1Z5", img=None,
        # No shop-wide terms: the existing invoice then prints its default terms, the
        # same ones customized invoices fall back to (keeps parity comparable).
        terms_and_conditions=None,
    )
    data.update(overrides)
    return NS(**data)


def make_client(**overrides):
    data = dict(client_name="Imani Olowe", phone_1_e164="+911234567890", client_phone_1="", address="Mukkam")
    data.update(overrides)
    return NS(**data)


def _dress_variant(name, code, attr="M"):
    category = NS(name="Dress")
    return NS(attribute=attr, product=NS(
        name=name, product_code=code, thumbnail_image=None, image=None,
        shop_service=NS(general_service=NS(main_category=category)),
    ))


def make_booking(n_items=2, cancelled=False, inclusive=False, item_name="Wedding Gown"):
    items = [
        NS(product_name=f"{item_name} {i}", category="Bridal", model="", color="Red", variant_attribute="M",
           quantity=1, amount=Decimal("1500"), product_thumbnail=None,
           product_variant=_dress_variant(f"{item_name} {i}", f"{i:05d}"),
           measurements=[{"name": "Chest", "value": "34"}, {"name": "Waist", "value": "28"}] if i == 1 else None)
        for i in range(1, n_items + 1)
    ]
    booking = NS(
        id=None, shop_booking_id=381, shop_sale_id=None, created_at=NOW,
        total_amount=Decimal(1500 * n_items + 300), security_amount=Decimal("1000"),
        discount_amount=Decimal("200"), advance_amount=Decimal("2000"),
        is_tax_applied=True, tax_name="GST", tax_rate=Decimal("18"), tax_amount=Decimal("120"),
        tax_calculation_type="inclusive" if inclusive else "exclusive",
        additional_charges=Rel([NS(name="Stitching", amount=Decimal("300"))]),
        security_amount_is_paid=True, delivery_status="cancelled" if cancelled else "booked",
        pickup_date=NOW, pickup_time=dt.time(9, 30), return_date=NOW + dt.timedelta(days=2), return_time=None,
        details={"location_from": "Showroom"}, staff_id=1, staff=NS(name="Ramees"), client_address="",
    )
    return booking, items


def make_sale(n_items=2):
    items = [
        NS(product_variant=NS(attribute="L", product=NS(
            name=f"Saree {i}", category="Silk", model="", color="Maroon",
            thumbnail_image=None, image=None, shop_service=None,
        )), price=Decimal("1000"), quantity=2, subtotal=Decimal("2000"))
        for i in range(1, n_items + 1)
    ]
    sale = NS(
        shop_sale_id="S-127", created_at=NOW, sale_date=NOW.date(), client_phone_e164="+919999999999",
        address="Calicut", total_amount=Decimal(2000 * n_items), discount=Decimal("100"),
        paid_amount=Decimal("1000"), is_tax_applied=False, tax_name="", tax_rate=0, tax_amount=0,
        tax_calculation_type="", additional_charges=Rel([]),
        payments=Rel([NS(get_payment_method_display=lambda: "UPI")]), staff_id=None,
    )
    return sale, items


def make_tailor_order(n_items=2, cancelled=False):
    items = [
        NS(product_name=f"Lehenga {i}", product_type="Stitching", quantity=1,
           customer_item_charge=Decimal("2500"), reference_image=None, created_product_variant=None,
           measurements={"Length": "40", "Hip": "36"} if i == 1 else None)
        for i in range(1, n_items + 1)
    ]
    order = NS(
        id=None, shop_tailor_id="T-45", created_at=NOW, client_phone_number="",
        additional_charges=Rel([NS(name="Embroidery", amount=Decimal("500"))]),
        discount_amount=Decimal("100"), paid_amount=Decimal("1000"), security_amount=Decimal("500"),
        is_tax_applied=False, tax_name="", tax_rate=0, tax_amount=0, tax_calculation_type="",
        security_payments=Rel([]), status="cancelled" if cancelled else "in_progress",
        customer_order_type="rental", pickup_date=NOW.date(), return_date=(NOW + dt.timedelta(days=5)).date(),
        staff_id=None,
    )
    return order, items


def numbering_sequence_mock(prefix="NWB"):
    """Patch target for shop.models.ShopNumberingSequence.objects (one query in the builders)."""
    seq = mock.MagicMock()
    seq.filter.return_value.values_list.return_value.first.return_value = prefix
    return seq
