from django.db import models
from django.core.validators import MinValueValidator
from services.models import ProductVariant
from .purchase import Purchase


class PurchaseItem(models.Model):
    """
    PurchaseItem represents individual line items in a purchase order.
    Only used when purchase_type = 'with_products'
    """
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="items",
        db_index=True
    )
    product_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        related_name="purchase_items"
    )

    # Snapshot fields for product information (preserved even if product is deleted)
    product_name = models.CharField(max_length=200, blank=True)

    # Variants stored separately (size, color, etc.)
    variant_size = models.CharField(max_length=50, blank=True, null=True)
    variant_color = models.CharField(max_length=50, blank=True, null=True)
    variant_other = models.CharField(max_length=100, blank=True, null=True)

    # Quantity and pricing
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    purchase_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Price per unit"
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Total for this line (quantity × purchase_amount)"
    )

    stock_increased = models.BooleanField(
        default=False,
        help_text="Whether this item's quantity was applied to ProductVariant.stock "
                   "at creation - determines whether destroy/update must reverse it."
    )

    update_product_cost = models.BooleanField(
        default=False,
        help_text="Whether this item's unit_purchase_price was applied to "
                   "Product.purchase_price at creation - stored for audit/"
                   "display purposes (existing-products only; new products "
                   "always set purchase_price at creation)."
    )

    line_group = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Shared identifier for all PurchaseItem rows created from a "
                   "single existing_products[]/new_products[] request entry - "
                   "lets multi-variant entries (one product, several variants) "
                   "be grouped for display/audit. NULL for rows created before "
                   "this field existed."
    )

    ITEM_SOURCE_CHOICES = [
        ("existing", "Existing Product"),
        ("new", "New Product"),
    ]
    item_source = models.CharField(
        max_length=10,
        choices=ITEM_SOURCE_CHOICES,
        default="existing",
        help_text="Whether this line item came from an existing_products[] "
                   "entry (product already existed) or a new_products[] entry "
                   "(Product was created inline as part of this purchase). "
                   "Rows created before this field existed default to "
                   "'existing' - not necessarily accurate for historical rows "
                   "created via new_products before this field was added."
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "purchase_item"
        verbose_name = "Purchase Item"
        verbose_name_plural = "Purchase Items"
        indexes = [
            models.Index(fields=['purchase']),
            models.Index(fields=['product_variant']),
        ]
        ordering = ['id']

    def __str__(self):
        return f"{self.product_name} x {self.quantity} - {self.purchase.purchase_code}"

    def save(self, *args, **kwargs):
        # Auto-calculate line_total
        self.line_total = self.quantity * self.purchase_amount

        # Snapshot product information
        if self.product_variant and not self.product_name:
            self.product_name = self.product_variant.product.name
            # Store variant attributes separately
            variant_attr = self.product_variant.attribute or ''
            # You can parse variant_attribute here if it has a specific format
            # For now, store in variant_other
            self.variant_other = variant_attr

        super().save(*args, **kwargs)
