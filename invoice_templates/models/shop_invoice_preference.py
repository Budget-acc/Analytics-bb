from django.conf import settings
from django.db import models


class ShopInvoicePreference(models.Model):
    """
    Per-shop invoice preferences, kept separate from template settings.

    - is_customization_enabled: the per-shop half of the legacy/new switch.
      Customized invoices are produced only when this AND the global
      INVOICE_CUSTOMIZATION_ENABLED setting are on; otherwise the existing
      pdf_template.py invoice is generated unchanged.
    - default_custom_invoice: the one customized invoice used for real
      invoices. null = Standard with default settings (today's look);
      deleting that customized invoice clears it automatically (SET_NULL).
    - *_terms: document-specific Terms & Conditions (list of strings).
      null/empty falls back to the default terms of that document type
      (pdf_template.DEFAULT_*_TERMS) - see v3/services/terms.py.
    """

    shop = models.OneToOneField(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="invoice_preference",
    )
    is_customization_enabled = models.BooleanField(
        default=False,
        help_text="Use the customized invoice template for this shop's real invoices.",
    )
    default_custom_invoice = models.ForeignKey(
        "invoice_templates.ShopCustomInvoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_preferences",
    )
    booking_terms = models.JSONField(null=True, blank=True)
    sale_terms = models.JSONField(null=True, blank=True)
    tailor_order_terms = models.JSONField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Invoice preference for shop {self.shop_id}"
