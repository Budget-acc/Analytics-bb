from django.db import models
from .product import Product
from .product_variant import ProductVariant
from .service_attribute_template import ServiceAttributeTemplate


class ServiceAttributeValue(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="attribute_values",
        null=True,
        blank=True
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="attribute_values",
        null=True,
        blank=True
    )

    attribute = models.ForeignKey(
        ServiceAttributeTemplate,
        on_delete=models.CASCADE
    )

    value = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["attribute"]),
            models.Index(fields=["product"]),
            models.Index(fields=["variant"]),
        ]

    def __str__(self):
        target = self.product or self.variant
        return f"{self.attribute.attribute_key}: {self.value} ({target})"
