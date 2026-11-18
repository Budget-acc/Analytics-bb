"""Keeps the InvoiceTemplate catalog rows in step with the code registry."""
from django.db import transaction


def sync_catalog(template_model=None):
    """
    Create missing catalog rows and refresh name/description/version/
    display_order of existing ones. `is_active` is only set on
    creation, so a template an admin switched off stays off.

    `template_model` lets data migrations pass their historical model.
    Returns (created_codes, updated_codes).
    """
    from invoice_templates.v3.registry import REGISTRY

    if template_model is None:
        from invoice_templates.models import InvoiceTemplate as template_model

    created, updated = [], []
    with transaction.atomic():
        existing = {row.code: row for row in template_model.objects.select_for_update().filter(code__in=REGISTRY)}
        for code, definition in REGISTRY.items():
            values = {
                "name": definition.name,
                "description": definition.description,
                "version": definition.version,
                "display_order": definition.display_order,
            }
            row = existing.get(code)
            if row is None:
                template_model.objects.create(code=code, is_active=True, **values)
                created.append(code)
                continue
            changed = [field for field, value in values.items() if getattr(row, field) != value]
            if changed:
                for field in changed:
                    setattr(row, field, values[field])
                row.save(update_fields=changed + ["updated_at"])
                updated.append(code)
    return created, updated
