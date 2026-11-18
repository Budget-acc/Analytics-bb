from invoice_templates.models import DocumentType
from invoice_templates.v3.services.configuration import resolve_for_shop
from invoice_templates.v3.services.formatting import format_datetime
from invoice_templates.v3.services.terms import document_terms, resolve_terms
from invoice_templates.v3.switch import is_globally_enabled


def preference_payload(shop_id) -> dict:
    """Full preference state for the active shop (1 indexed query)."""
    resolved = resolve_for_shop(shop_id)  # preference + default invoice + template, one query
    preference = resolved.preference
    invoice = resolved.custom_invoice

    enabled = bool(preference and preference.is_customization_enabled)
    doc_types = [choice.value for choice in DocumentType]
    return {
        "is_customization_enabled": enabled,
        "global_customization_enabled": is_globally_enabled(),
        "uses_customized_invoices": is_globally_enabled() and enabled,
        "default_custom_invoice": (
            {"id": invoice.id, "name": invoice.name, "template_code": invoice.template.code}
            if invoice is not None else None
        ),
        "effective_template_code": resolved.definition.code,
        # What the shop set itself (null = not set for that document type).
        "terms": {doc: document_terms(preference, doc) for doc in doc_types},
        # What invoices actually print: the shop's terms, else the default terms.
        "effective_terms": {doc: resolve_terms(None, doc, preference) for doc in doc_types},
        "updated_at": format_datetime(preference.updated_at) if preference else None,
    }
