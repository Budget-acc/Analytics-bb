from django.db import models
from .sale import Sale


class SaleItem(models.Model):

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="items"
    )
    product_variant = models.ForeignKey(
        "services.ProductVariant",
        on_delete=models.CASCADE,
        related_name="sale_items",
    )
    quantity = models.PositiveIntegerField(default=1)
    price = models.PositiveIntegerField(help_text="Selling price at the time of sale")
    subtotal = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product_variant} x {self.quantity}"
