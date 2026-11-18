from django.db import models

from .shop_whatsapp_configuration import ShopWhatsAppConfiguration


class ShopWhatsAppTemplate(models.Model):
    """
    Phase 1 (models only): tracks Meta message-template approval per template,
    since approval is asynchronous and independent per template (one of the
    platform's canonical templates can be approved while another is rejected)
    - a single boolean on ShopWhatsAppConfiguration can't represent that.
    Nothing yet writes to this table (template registration is a future phase).
    """

    shop_whatsapp_configuration = models.ForeignKey(
        ShopWhatsAppConfiguration,
        related_name="templates",
        on_delete=models.CASCADE,
        help_text="The shop connection this template registration belongs to.",
    )
    template_name = models.CharField(
        max_length=64,
        help_text=(
            "Canonical template name as used in the codebase and registered with Meta, e.g. "
            "'sale_invoice_2'. Matches the naming already used in "
            "whatsapp_integration/v3/services.py's Wacto-based templates."
        ),
    )
    meta_template_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text=(
            "Meta's own ID for this template once it has been submitted via the Message Templates "
            "API. Blank until submission (future phase)."
        ),
    )
    language = models.CharField(
        max_length=8,
        default="en",
        null=True,
        blank=True,
        help_text="Template language code, matching the 'en' used by the existing Wacto templates.",
    )
    category = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        help_text=(
            "Meta template category (e.g. UTILITY, MARKETING). FLAGGED AS UNCERTAIN: the exact "
            "category value for each of the platform's 5 templates has not been determined yet — "
            "this should be set correctly per-template during future-phase template registration, "
            "not guessed here."
        ),
    )
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("PAUSED", "Paused"),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
        help_text="Meta's current approval status for this specific template on this shop's WABA.",
    )

    class Meta:
        db_table = "whatsapp_shop_template"
        unique_together = ("shop_whatsapp_configuration", "template_name")

    def __str__(self):
        return f"{self.template_name} ({self.status}) for {self.shop_whatsapp_configuration.shop.name}"
