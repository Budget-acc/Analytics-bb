"""
Writes for shop invoice preferences (default customized invoice, the
customization switch and document-specific terms).

All writes are scoped by shop_id (resolved by the view from the
authenticated request) and are race-safe: database unique constraints plus
update_or_create inside a transaction, retried once on a concurrent insert.
"""
from django.db import IntegrityError, transaction

from invoice_templates.models import DocumentType, ShopInvoicePreference

from .terms import MAX_TERM_LENGTH, MAX_TERMS, PREFERENCE_TERMS_FIELD


class TermsError(ValueError):
    def __init__(self, errors):
        super().__init__("Invalid terms format.")
        self.errors = errors


def _user_ref(user):
    return user if getattr(user, "pk", None) else None


def _update_or_create(model, lookup, defaults):
    """update_or_create, retried once if a concurrent request inserted the row first."""
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                return model.objects.update_or_create(**lookup, defaults=defaults)
        except IntegrityError:
            if attempt == 2:
                raise


def set_default_custom_invoice(shop_id, invoice, user):
    """
    Select the customized invoice used for real invoices (must belong to the
    shop - the caller resolves it shop-scoped). Selecting one also switches
    customization on; None goes back to Standard with default settings.
    """
    defaults = {"default_custom_invoice": invoice, "updated_by": _user_ref(user)}
    if invoice is not None:
        defaults["is_customization_enabled"] = True
    preference, _ = _update_or_create(ShopInvoicePreference, {"shop_id": shop_id}, defaults)
    return preference


def set_customization_enabled(shop_id, enabled, user):
    preference, _ = _update_or_create(
        ShopInvoicePreference,
        {"shop_id": shop_id},
        {"is_customization_enabled": enabled, "updated_by": _user_ref(user)},
    )
    return preference


def validate_terms_payload(data) -> dict:
    """
    {booking|sale|tailor_order: [str, ...] | null} -> {preference_field: list|None}.
    An empty list or null clears the document terms (falls back to the
    default terms of that type). Raises TermsError with per-key messages.
    """
    if not isinstance(data, dict):
        raise TermsError({"request": ["Request body must be an object."]})
    allowed = {choice.value for choice in DocumentType}
    errors, updates = {}, {}
    for key, value in data.items():
        if key not in allowed:
            errors[str(key)] = [f"'{key}' is not a document type. Allowed: booking, sale, tailor_order."]
            continue
        if value is None:
            updates[PREFERENCE_TERMS_FIELD[key]] = None
            continue
        if not isinstance(value, list):
            errors[key] = ["Must be a list of strings or null."]
            continue
        if len(value) > MAX_TERMS:
            errors[key] = [f"At most {MAX_TERMS} terms are allowed."]
            continue
        cleaned, item_errors = [], []
        for index, term in enumerate(value):
            if not isinstance(term, str) or not term.strip():
                item_errors.append(f"Item {index + 1} must be non-empty text.")
            elif len(term.strip()) > MAX_TERM_LENGTH:
                item_errors.append(f"Item {index + 1} must be at most {MAX_TERM_LENGTH} characters.")
            else:
                cleaned.append(term.strip())
        if item_errors:
            errors[key] = item_errors
        else:
            updates[PREFERENCE_TERMS_FIELD[key]] = cleaned or None
    if errors:
        raise TermsError(errors)
    return updates


def update_terms(shop_id, updates, user):
    defaults = dict(updates)
    defaults["updated_by"] = _user_ref(user)
    preference, _ = _update_or_create(ShopInvoicePreference, {"shop_id": shop_id}, defaults)
    return preference
