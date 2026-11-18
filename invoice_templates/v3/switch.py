"""
The switch between the existing invoice generation and customized templates.

Customized invoices are produced only when BOTH are on:
  1. settings.INVOICE_CUSTOMIZATION_ENABLED       (global, env-driven)
  2. ShopInvoicePreference.is_customization_enabled (per shop)
Otherwise the existing whatsapp_integration/v3/pdf_template.py logic runs
unchanged. Flipping either switch never deletes any saved data.
"""
from django.conf import settings

from invoice_templates.models import ShopInvoicePreference


def is_globally_enabled() -> bool:
    return bool(getattr(settings, "INVOICE_CUSTOMIZATION_ENABLED", False))


def is_customization_enabled(shop) -> bool:
    shop_id = getattr(shop, "id", None)
    if not shop_id or not is_globally_enabled():
        return False  # no query when globally off
    return ShopInvoicePreference.objects.filter(
        shop_id=shop_id, is_customization_enabled=True
    ).exists()
