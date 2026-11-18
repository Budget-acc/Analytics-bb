from whatsapp_integration.models import ShopWhatsAppTemplate
from whatsapp_integration.api.admin.serializers import ShopWhatsAppTemplateWriteSerializer


def validate_template_entries(configuration, templates_data, allow_update=True):
    """
    Validates (without saving) a list of template entry dicts against `configuration`.

    Each entry may include "id" to target an existing template (only when
    allow_update=True) or omit it to create a new one. Creating a new template
    requires `configuration.verified` to be True (business rule: templates can
    only be added once the connection has been verified with a real test send).

    Returns (has_error, errors, prepared):
      - has_error: bool — True if any entry failed its checks or serializer validation.
      - errors: list aligned by index with `templates_data`; None for valid entries,
        else a dict describing what's wrong with that entry.
      - prepared: list aligned by index; None for invalid entries, else a
        (serializer, is_create) tuple for valid ones, ready for
        `save_prepared_templates()` — no DB writes have happened yet.

    Two-phase design (validate here, save separately in `save_prepared_templates`)
    is deliberate: it lets callers validate configuration fields and template
    entries together and only commit anything once *everything* in the request
    is confirmed valid — an "all or nothing" save for a combined request.
    """
    errors = []
    prepared = []
    has_error = False
    seen_names = set()

    for entry in templates_data:
        entry = dict(entry) if isinstance(entry, dict) else {}
        template_id = entry.pop("id", None)
        instance = None

        if template_id is not None:
            if not allow_update:
                has_error = True
                errors.append({
                    "id": [
                        "id is not allowed when creating a template; use the combined "
                        "Update endpoint (PATCH .../configurations/<pk>/) to modify an "
                        "existing template."
                    ]
                })
                prepared.append(None)
                continue

            try:
                instance = ShopWhatsAppTemplate.objects.select_for_update().get(
                    id=template_id, shop_whatsapp_configuration=configuration
                )
            except ShopWhatsAppTemplate.DoesNotExist:
                has_error = True
                errors.append({"id": [f"Template id {template_id} does not belong to this configuration."]})
                prepared.append(None)
                continue
        else:
            if not configuration.verified:
                has_error = True
                errors.append({
                    "non_field_errors": ["Verify the WhatsApp configuration before adding templates."]
                })
                prepared.append(None)
                continue

            template_name = entry.get("template_name")
            if template_name:
                if template_name in seen_names:
                    has_error = True
                    errors.append({"template_name": [f"Duplicate template_name '{template_name}' in this request."]})
                    prepared.append(None)
                    continue

                if ShopWhatsAppTemplate.objects.filter(
                    shop_whatsapp_configuration=configuration, template_name=template_name
                ).exists():
                    has_error = True
                    errors.append({
                        "template_name": [f"A template named '{template_name}' already exists for this configuration."]
                    })
                    prepared.append(None)
                    continue

                seen_names.add(template_name)

        serializer = ShopWhatsAppTemplateWriteSerializer(instance, data=entry, partial=instance is not None)
        if not serializer.is_valid():
            has_error = True
            errors.append(serializer.errors)
            prepared.append(None)
            continue

        errors.append(None)
        prepared.append((serializer, instance is None))

    return has_error, errors, prepared


def save_prepared_templates(configuration, prepared):
    """
    Saves the (serializer, is_create) tuples returned by `validate_template_entries`.
    Call only after confirming `has_error` was False for the whole request.
    """
    saved = []
    for item in prepared:
        serializer, is_create = item
        if is_create:
            saved.append(serializer.save(shop_whatsapp_configuration=configuration))
        else:
            saved.append(serializer.save())
    return saved
