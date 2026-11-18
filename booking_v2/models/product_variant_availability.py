from django.db import models


class ProductVariantAvailability(models.Model):
    variant = models.ForeignKey(
        "services.ProductVariant", on_delete=models.CASCADE, db_index=True
    )
    date = models.DateField()
    booked_quantity = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("variant", "date")
        indexes = [
            models.Index(fields=["variant", "date"]),
        ]

    def __str__(self):
        return f"{self.variant.product.name} - {self.variant.attribute or ''} - {self.date} - Booked: {self.booked_quantity}"
