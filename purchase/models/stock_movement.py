from django.db import models
from shop.models import Shop
from authentication.models import User
from services.models import ProductVariant
from .purchase import Purchase


class StockMovement(models.Model):
    """
    StockMovement records every change in product stock for auditing.
    Created automatically when purchase with products is made.
    """
    MOVEMENT_TYPE_CHOICES = [
        ('purchase', 'Purchase'),
        ('sale', 'Sale'),
        ('return', 'Return'),
        ('adjustment', 'Adjustment'),
        ('damage', 'Damage'),
    ]

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="stock_movements",
        db_index=True
    )
    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stock_movements"
    )
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements"
    )

    # Stock tracking
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES, db_index=True)
    previous_stock = models.IntegerField(help_text="Stock before this movement")
    quantity_changed = models.IntegerField(help_text="Quantity added (positive) or removed (negative)")
    new_stock = models.IntegerField(help_text="Stock after this movement")

    # Reference
    reference_number = models.CharField(max_length=50, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    # User who performed the action
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stock_movements"
    )

    # Timestamp
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "stock_movement"
        verbose_name = "Stock Movement"
        verbose_name_plural = "Stock Movements"
        indexes = [
            models.Index(fields=['shop', 'created_at']),
            models.Index(fields=['product_variant', 'created_at']),
            models.Index(fields=['movement_type']),
            models.Index(fields=['purchase']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.movement_type} - {self.product_variant} ({self.quantity_changed:+d})"
