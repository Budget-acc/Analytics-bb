from django.db import models
from .general_service import GeneralService


class ServiceAttributeTemplate(models.Model):
    general_service = models.ForeignKey(GeneralService, on_delete=models.CASCADE, related_name="attribute_templates")
    attribute_key = models.SlugField(max_length=100)
    attribute_label = models.CharField(max_length=100)

    attribute_type = models.CharField(
        max_length=20,
        choices=[
            ('text', 'Text'),
            ('number', 'Number'),
            ('decimal', 'Decimal'),
            ('boolean', 'Boolean'),
            ('choice', 'Choice'),
            ('date', 'Date'),
        ]
    )

    choices = models.JSONField(null=True, blank=True)
    applies_to = models.CharField(
        max_length=20,
        choices=[('product', 'Product'), ('variant', 'Variant')],
        default='product'
    )

    is_required = models.BooleanField(default=False)
    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["display_order"]
        indexes = [
            models.Index(fields=["general_service"]),
            models.Index(fields=["general_service", "applies_to"]),
            models.Index(fields=["general_service", "display_order"]),
            models.Index(fields=["attribute_key"]),
        ]

    def __str__(self):
        return f"{self.general_service.name} - {self.attribute_label}"
