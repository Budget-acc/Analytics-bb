from django.conf import settings
from django.db import models
from django.db.models import Q


class InvoiceTemplateShare(models.Model):
    """
    A share code that lets another shop import a copy of one of this shop's
    customized invoices.

    The name and configuration are snapshotted when the code is generated, so
    the code always imports exactly what was shared (later edits by the source
    shop do not leak through) and always targets `template`. Importing creates
    a NEW customized invoice in the receiving shop. Codes are globally unique
    (DB constraint) and at most one code per customized invoice is active at a
    time (partial unique constraint). Deleting the customized invoice deletes
    its codes (CASCADE), so they can no longer be imported.
    """

    code = models.CharField(
        max_length=12,
        unique=True,
        help_text="Normalized share code (uppercase, no separators).",
    )
    source_shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="invoice_template_shares",
    )
    source_custom_invoice = models.ForeignKey(
        "invoice_templates.ShopCustomInvoice",
        on_delete=models.CASCADE,
        related_name="shares",
    )
    template = models.ForeignKey(
        "invoice_templates.InvoiceTemplate",
        on_delete=models.PROTECT,
        related_name="shares",
    )
    invoice_name = models.CharField(max_length=60, help_text="Name of the customized invoice when shared.")
    configuration_snapshot = models.JSONField(default=dict)
    template_version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    import_count = models.PositiveIntegerField(default=0)
    last_imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_custom_invoice"],
                condition=Q(revoked_at__isnull=True),
                name="uniq_active_custom_invoice_share",
            )
        ]

    def __str__(self):
        return f"{self.code} (shop {self.source_shop_id}, custom invoice {self.source_custom_invoice_id})"
