from django.db import models
from django.db.models import Index
from django.utils import timezone
from django.core.validators import MinValueValidator
from .tailor_order_item import TailorOrderItem


class TailorMaterialUsage(models.Model):

    order_item = models.ForeignKey(TailorOrderItem, related_name='material_usages', on_delete=models.CASCADE)
    material = models.ForeignKey('services.ProductVariant', related_name='usages', on_delete=models.CASCADE)
    quantity_meters = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(0)])
    total_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            Index(fields=['order_item']),
            Index(fields=['material']),
        ]

    def __str__(self):
        return f"{self.material.product.name} - {self.quantity_meters}m for item {self.order_item.id}"
