from invoice_templates.models import ShopInvoicePreference
from invoice_templates.v3.registry import get_definition
from invoice_templates.v3.services.configuration import effective_configuration
from invoice_templates.v3.services.formatting import format_datetime

from ..invoice_template.helpers import OPTIONS_EXAMPLE, OPTIONS_SHAPE, template_options_payload

LIST_ITEM_SHAPE = (
    "  - id (integer): customized invoice id.\n"
    "  - name (string): the shop's name for it (shown in the app only, not printed).\n"
    "  - template_code (string), template_name (string): the predefined template it is built from.\n"
    "  - is_default (boolean): used for the shop's real invoices.\n"
    "  - created_at, updated_at (string, \"YYYY-MM-DD HH:MM:SS\", IST)."
)

USAGE_SHAPE = (
    "- usage (object): used (integer) - customized invoices the shop has; limit (integer) - "
    "maximum it can create (4); remaining (integer) - how many more it can create."
)

DETAIL_SHAPE = (
    "- id (integer), name (string), is_default (boolean), created_at, updated_at (string, \"YYYY-MM-DD HH:MM:SS\", IST).\n"
    "- template (object): id, code, name.\n"
    "- configuration (object): the shop's settings used by real invoices and previews - colors "
    "{background, primary, text, border, cancelled} (each \"#RRGGBB\") and font (string).\n"
    + OPTIONS_SHAPE
)

ITEM_EXAMPLE = {
    "id": 42, "name": "Wedding Season", "template_code": "classic", "template_name": "Classic",
    "is_default": True, "created_at": "2026-10-16 10:02:11", "updated_at": "2026-10-16 10:20:45",
}

DETAIL_EXAMPLE = {
    "id": 42, "name": "Wedding Season", "is_default": True,
    "created_at": "2026-10-16 10:02:11", "updated_at": "2026-10-16 10:20:45",
    "template": {"id": 2, "code": "classic", "name": "Classic"},
    "configuration": {
        "colors": {"background": "#FFF8F0", "primary": "#8B1E3F", "text": "#1F1F1F",
                   "border": "#8B1E3F", "cancelled": "#D32F2F"},
        "font": "inter",
    },
    **OPTIONS_EXAMPLE,
}


def default_invoice_id(shop_id):
    return (
        ShopInvoicePreference.objects.filter(shop_id=shop_id)
        .values_list("default_custom_invoice_id", flat=True).first()
    )


def invoice_summary(invoice, default_id) -> dict:
    return {
        "id": invoice.id,
        "name": invoice.name,
        "template_code": invoice.template.code,
        "template_name": invoice.template.name,
        "is_default": invoice.id == default_id,
        "created_at": format_datetime(invoice.created_at),
        "updated_at": format_datetime(invoice.updated_at),
    }


def invoice_detail(invoice, default_id) -> dict:
    definition = get_definition(invoice.template.code)
    return {
        "id": invoice.id,
        "name": invoice.name,
        "is_default": invoice.id == default_id,
        "created_at": format_datetime(invoice.created_at),
        "updated_at": format_datetime(invoice.updated_at),
        "template": {"id": invoice.template.id, "code": invoice.template.code, "name": invoice.template.name},
        "configuration": effective_configuration(definition, invoice.configuration, invoice.template_version),
        **template_options_payload(definition),
    }
