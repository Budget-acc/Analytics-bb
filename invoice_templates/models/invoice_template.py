from django.db import models


class InvoiceTemplate(models.Model):
    """
    Global catalog row for a predefined invoice template.

    The template's customization schema, default values and renderer live in
    code (invoice_templates/v3/registry/<code>/) so they can never drift from
    the rendering logic. This row exists so shop settings, preferences and
    share codes can reference a template with a real foreign key, and so a
    template can be switched off without a deploy. Rows are created/updated by
    the seed migration and the `sync_invoice_templates` command - never by
    shop users - and are deactivated rather than deleted.
    """

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Stable identifier matching the code registry (e.g. 'standard', 'classic').",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(
        default=1,
        help_text="Current version of the template definition. Mirrors the code registry.",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive templates are hidden and cannot be selected, saved, previewed or imported.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.code})"
