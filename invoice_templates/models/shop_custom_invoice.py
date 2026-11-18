from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class ShopCustomInvoice(models.Model):
    """
    A customized invoice created by a shop: a shop-chosen name, one of the
    predefined templates, and the shop's settings for it.

    - `name` only helps the shop tell its customized invoices apart in the app;
      it is never printed on the PDF. Unique per shop (case-insensitive).
    - A shop may create several customized invoices from the same template.
    - The number a shop may create is capped (settings.INVOICE_CUSTOM_INVOICE_LIMIT,
      enforced in v3/services/custom_invoices.py); updates are unlimited.
    - `configuration` is a flat dict keyed by the template's field keys
      (e.g. {"color.accent": "#6C5CE7", "size.title": 32}) and only ever holds
      values that passed strict validation against the template's schema.
    """

    shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="custom_invoices",
    )
    template = models.ForeignKey(
        "invoice_templates.InvoiceTemplate",
        on_delete=models.PROTECT,
        related_name="custom_invoices",
    )
    name = models.CharField(
        max_length=60,
        help_text="Shop-chosen name used to identify this customized invoice in the app (not printed).",
    )
    configuration = models.JSONField(default=dict)
    template_version = models.PositiveIntegerField(
        default=1,
        help_text="Template definition version these settings were validated against.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "shop",
                name="uniq_shop_custom_invoice_name_ci",
            )
        ]
        indexes = [
            models.Index(fields=["shop", "created_at"], name="custom_invoice_shop_idx"),
        ]

    def __str__(self):
        return f"{self.name} (shop {self.shop_id}, template {self.template_id})"
