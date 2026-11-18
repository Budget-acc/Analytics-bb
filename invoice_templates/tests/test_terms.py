from types import SimpleNamespace as NS

from django.test import SimpleTestCase

from invoice_templates.v3.services.preferences import TermsError, validate_terms_payload
from invoice_templates.v3.services.terms import BUILTIN_TERMS, resolve_terms
from whatsapp_integration.v3 import pdf_template


class ResolveTermsTests(SimpleTestCase):
    def test_document_terms_win(self):
        shop = NS(terms_and_conditions=["General"])
        pref = NS(booking_terms=["Booking only"], sale_terms=None, tailor_order_terms=[])
        self.assertEqual(resolve_terms(shop, "booking", pref), ["Booking only"])

    def test_falls_back_to_default_terms_per_document_type(self):
        # Shop-wide Shop.terms_and_conditions is NOT used for customized invoices.
        shop = NS(terms_and_conditions=["General"])
        pref = NS(booking_terms=["Booking only"], sale_terms=None, tailor_order_terms=[])
        self.assertEqual(resolve_terms(shop, "sale", pref), list(pdf_template.DEFAULT_SALE_TERMS))
        self.assertEqual(resolve_terms(shop, "tailor_order", pref), list(pdf_template.DEFAULT_TAILOR_ORDER_TERMS))
        self.assertEqual(resolve_terms(shop, "booking", None), list(pdf_template.DEFAULT_BOOKING_TERMS))

    def test_defaults_are_the_existing_pdf_defaults_not_a_copy(self):
        self.assertIs(BUILTIN_TERMS["booking"], pdf_template.DEFAULT_BOOKING_TERMS)
        self.assertIs(BUILTIN_TERMS["sale"], pdf_template.DEFAULT_SALE_TERMS)
        self.assertIs(BUILTIN_TERMS["tailor_order"], pdf_template.DEFAULT_TAILOR_ORDER_TERMS)
        self.assertEqual(pdf_template.DEFAULT_BOOKING_TERMS[0], "No refund will be given after Booking cancellation.")
        self.assertEqual(
            (len(pdf_template.DEFAULT_BOOKING_TERMS), len(pdf_template.DEFAULT_SALE_TERMS),
             len(pdf_template.DEFAULT_TAILOR_ORDER_TERMS)),
            (6, 4, 4),
        )

    def test_result_is_a_copy(self):
        resolve_terms(None, "sale", None).append("mutated")
        self.assertNotIn("mutated", BUILTIN_TERMS["sale"])


class TermsPayloadTests(SimpleTestCase):
    def test_valid_payload(self):
        updates = validate_terms_payload({"booking": ["  A  ", "B"], "sale": None, "tailor_order": []})
        self.assertEqual(updates, {"booking_terms": ["A", "B"], "sale_terms": None, "tailor_order_terms": None})

    def test_invalid_payload(self):
        with self.assertRaises(TermsError) as ctx:
            validate_terms_payload({
                "booking": "not a list", "sale": ["ok", ""], "rent": ["x"], "tailor_order": ["x"] * 31,
            })
        self.assertEqual(set(ctx.exception.errors), {"booking", "sale", "rent", "tailor_order"})

    def test_term_length_limit(self):
        with self.assertRaises(TermsError):
            validate_terms_payload({"booking": ["x" * 301]})
