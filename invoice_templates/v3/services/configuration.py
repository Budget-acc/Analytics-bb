"""
Template configuration: validation, version migration, merging with
defaults, and resolution of a shop's effective template + settings.

Shared by save, preview, import and real invoice generation so the rules
are identical everywhere.
"""
import copy
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from invoice_templates.models import InvoiceTemplate, ShopCustomInvoice, ShopInvoicePreference
from invoice_templates.v3.registry import DEFAULT_TEMPLATE_CODE, get_definition
from invoice_templates.v3.registry.base import TemplateDefinition
from invoice_templates.v3.registry.fields import (
    COLOR_KEYS, ERR_COLORS_OBJECT, ERR_UNKNOWN_FIELD, validate_color, validate_font,
)

logger = logging.getLogger("api_requests")


class ConfigurationError(ValueError):
    """Strict validation failed. `errors` maps field key -> [messages]."""

    def __init__(self, errors: Dict[str, List[str]]):
        super().__init__("Invalid template settings.")
        self.errors = errors


def migrate_configuration(definition: TemplateDefinition, configuration: dict, from_version: int) -> dict:
    """Apply the definition's migrations from `from_version` up to its current version."""
    config = dict(configuration or {})
    version = from_version or 1
    while version < definition.version:
        migrate = definition.migrations.get(version)
        if migrate:
            config = migrate(config)
        version += 1
    return config


def validate_configuration(definition: TemplateDefinition, raw, base: Optional[dict] = None) -> dict:
    """
    Strict validation (create / update / preview / import) of a PARTIAL
    configuration {"colors": {...}, "font": "..."}. The submitted values are
    merged onto `base` (a complete configuration) or the template defaults,
    and the COMPLETE result is returned. Raises ConfigurationError listing
    every problem at once (keys: "colors.<name>", "font", "colors", "<unknown>").
    """
    if not isinstance(raw, dict):
        raise ConfigurationError({"configuration": ["Must be an object with colors and/or font."]})

    result = effective_configuration(definition, base) if base else definition.defaults
    errors = {}
    for key, value in raw.items():
        if key == "colors":
            if not isinstance(value, dict):
                errors["colors"] = [ERR_COLORS_OBJECT]
                continue
            for color_key, color_value in value.items():
                if color_key not in COLOR_KEYS:
                    errors[f"colors.{color_key}"] = [ERR_UNKNOWN_FIELD.format(key=f"colors.{color_key}")]
                    continue
                clean, error = validate_color(color_key, color_value)
                if error:
                    errors[f"colors.{color_key}"] = [error]
                else:
                    result["colors"][color_key] = clean
        elif key == "font":
            clean, error = validate_font(value)
            if error:
                errors["font"] = [error]
            else:
                result["font"] = clean
        else:
            errors[str(key)] = [ERR_UNKNOWN_FIELD.format(key=key)]
    if errors:
        raise ConfigurationError(errors)
    return result


def effective_configuration(definition: TemplateDefinition, saved: Optional[dict] = None,
                            saved_version: Optional[int] = None) -> dict:
    """
    Tolerant merge used for rendering: the template defaults overlaid with
    every saved value that is still valid. Unknown or invalid values are
    dropped instead of failing the invoice. Always returns all six values.
    """
    config = definition.defaults
    if not isinstance(saved, dict) or not saved:
        return config
    migrated = migrate_configuration(definition, saved, saved_version or definition.version)
    colors = migrated.get("colors")
    if isinstance(colors, dict):
        for key in COLOR_KEYS:
            clean, error = validate_color(key, colors.get(key))
            if error is None:
                config["colors"][key] = clean
    clean_font, error = validate_font(migrated.get("font"))
    if error is None:
        config["font"] = clean_font
    return config


@dataclass
class ResolvedTemplate:
    custom_invoice: Optional[ShopCustomInvoice]  # None -> Standard with default settings
    definition: TemplateDefinition
    configuration: dict                          # effective (defaults + saved)
    preference: Optional[ShopInvoicePreference]


def get_active_template(code_or_id) -> Optional[InvoiceTemplate]:
    """
    Active catalog row that also exists in the code registry, looked up by
    its code ("classic") or its numeric id (2 / "2"). One query either way.
    """
    if code_or_id is None or isinstance(code_or_id, bool):
        return None
    value = str(code_or_id).strip()
    if not value:
        return None
    if value.isdigit():
        template = InvoiceTemplate.objects.filter(id=int(value), is_active=True).first()
        return template if template is not None and get_definition(template.code) is not None else None
    if get_definition(value) is None:
        return None
    return InvoiceTemplate.objects.filter(code=value, is_active=True).first()


def is_usable_template(template) -> bool:
    return template is not None and template.is_active and get_definition(template.code) is not None


def resolve_for_shop(shop_id, preference=None, preference_loaded=False) -> ResolvedTemplate:
    """
    The shop's default customized invoice and its effective configuration, or
    Standard with its default settings when none is selected (or its template
    was deactivated). One indexed query (preference + customized invoice +
    template via select_related), none when the preference is passed in.
    """
    if not preference_loaded:
        preference = (
            ShopInvoicePreference.objects.select_related("default_custom_invoice__template")
            .filter(shop_id=shop_id).first()
        )

    invoice = getattr(preference, "default_custom_invoice", None) if preference else None
    if invoice is not None and not is_usable_template(invoice.template):
        logger.warning(
            f"Shop {shop_id} default customized invoice {invoice.id} uses unavailable template "
            f"'{invoice.template.code}'; using Standard."
        )
        invoice = None

    if invoice is None:
        definition = get_definition(DEFAULT_TEMPLATE_CODE)
        configuration = effective_configuration(definition)
    else:
        definition = get_definition(invoice.template.code)
        configuration = effective_configuration(definition, invoice.configuration, invoice.template_version)

    return ResolvedTemplate(
        custom_invoice=invoice,
        definition=definition,
        configuration=configuration,
        preference=preference,
    )


def snapshot(configuration: dict) -> dict:
    """Independent deep copy (so shared/imported JSON never aliases)."""
    return copy.deepcopy(configuration)


def split_errors(errors: Dict[str, List[str]]) -> Tuple[str, ...]:
    """Flat tuple of messages (for logging / dev_message)."""
    return tuple(msg for messages in errors.values() for msg in messages)
