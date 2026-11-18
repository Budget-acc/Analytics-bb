"""
Terms & Conditions resolution per document type (customized invoices).

For each document type independently (booking / sale / tailor_order):
  1. ShopInvoicePreference.<doc>_terms  - the terms the shop set for that type
  2. otherwise the default terms of the existing invoice PDF for that type
     (DEFAULT_*_TERMS in whatsapp_integration/v3/pdf_template.py - imported,
     never copied, so there is one source for the default content).

Used for real customized invoices, previews and the preference API's
effective_terms. The existing (non-customized) invoices keep their own logic.
"""
from invoice_templates.models import DocumentType
from whatsapp_integration.v3.pdf_template import (
    DEFAULT_BOOKING_TERMS, DEFAULT_SALE_TERMS, DEFAULT_TAILOR_ORDER_TERMS,
)

BUILTIN_TERMS = {
    DocumentType.BOOKING: DEFAULT_BOOKING_TERMS,
    DocumentType.SALE: DEFAULT_SALE_TERMS,
    DocumentType.TAILOR_ORDER: DEFAULT_TAILOR_ORDER_TERMS,
}

PREFERENCE_TERMS_FIELD = {
    DocumentType.BOOKING: "booking_terms",
    DocumentType.SALE: "sale_terms",
    DocumentType.TAILOR_ORDER: "tailor_order_terms",
}

MAX_TERMS = 30
MAX_TERM_LENGTH = 300


def _usable(terms) -> bool:
    return isinstance(terms, list) and len(terms) > 0


def document_terms(preference, document_type):
    """The shop's own terms for this document type, or None when not set."""
    if preference is None:
        return None
    terms = getattr(preference, PREFERENCE_TERMS_FIELD[document_type], None)
    return terms if _usable(terms) else None


def resolve_terms(shop, document_type, preference=None) -> list:
    """
    Final terms for one document type: the shop's own terms, else the
    existing default terms for that type. `preference` is passed in by
    callers that already loaded it, so this never queries the database.
    (`shop` is kept for call-site compatibility.)
    """
    terms = document_terms(preference, document_type)
    if terms:
        return [str(term) for term in terms]
    return list(BUILTIN_TERMS[document_type])
