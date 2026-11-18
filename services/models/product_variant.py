from decimal import Decimal

from django.db import models
from django.utils import timezone
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .product import Product
from shop.models import Shop
from django.db.models import Q

class ProductVariant(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey(
        Shop,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_variants",
        db_index=True,
    )    

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants"
    )
    attribute = models.CharField(max_length=50, blank=True)  # e.g., 30, 32, M, L
    price = models.PositiveIntegerField(null=True, blank=True)
    stock = models.PositiveIntegerField(default=0)
    fabric_length = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Available fabric length in meters (for material products)"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    unique_code = models.CharField(max_length=64, unique=True, db_index=True, blank=True, null=True)
    external_qr_code = models.CharField(
        max_length=128,
        db_index=True,
        blank=True,
        null=True,
        help_text="Customer's existing QR / barcode value"
    )

    structure_updated_at = models.DateTimeField(auto_now_add=True)  # Name, attribute changes
    quantity_updated_at = models.DateTimeField(auto_now=True)       # Stock, fabric changes

    # Out-of-service amounts (damage / maintenance / other). Moved out of and
    # back into `stock` / `fabric_length` only by the stock-adjustment API
    # (services/utils/stock_adjustment.py); every existing availability check
    # keeps reading `stock` / `fabric_length` as the usable amount.
    # editable=False keeps them out of admin forms and ModelForms.
    unavailable_stock = models.PositiveIntegerField(
        default=0, editable=False,
        help_text="Units moved out of service via the stock-adjustment API. Not included in `stock`."
    )
    unavailable_fabric_length = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"), editable=False,
        help_text="Fabric length (m) moved out of service via the stock-adjustment API. Not included in `fabric_length`."
    )

    class Meta:
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["attribute"]),
            models.Index(fields=["stock"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["structure_updated_at"]),
            models.Index(fields=["quantity_updated_at"]),
            models.Index(fields=['product', 'is_active']),
            models.Index(fields=['is_active', 'stock']),
            models.Index(fields=['product_id', 'is_active', 'stock']),
            models.Index(fields=['external_qr_code', 'is_active']),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=['shop', 'external_qr_code'],
                condition=Q(deleted__isnull=True) & Q(external_qr_code__isnull=False),
                name='unique_external_qr_per_shop',
            ),
            models.CheckConstraint(
                check=Q(unavailable_fabric_length__gte=0),
                name='variant_unavailable_fabric_non_negative',
            ),
        ]

    def save(self, *args, **kwargs):
        """
        Smart save: Only update structure_updated_at when structure fields change.
        """
        if not self.shop_id and self.product_id:
            self.shop_id = self.product.shop_service.shop_id    

        update_fields = kwargs.get('update_fields', None)

        # Quantity-only fields (don't trigger structure changes)
        quantity_only_fields = {'stock', 'fabric_length', 'quantity_updated_at'}

        if update_fields:
            # Convert to list if it's a tuple
            if isinstance(update_fields, tuple):
                update_fields = list(update_fields)

            # Check if ONLY quantity fields are being updated
            if set(update_fields).issubset(quantity_only_fields):
                # Don't update structure_updated_at - only quantity changes
                pass
            else:
                self.structure_updated_at = timezone.now()

                # Add structure_updated_at to update_fields if not already present
                if 'structure_updated_at' not in update_fields:
                    update_fields.append('structure_updated_at')
                    kwargs['update_fields'] = update_fields
        else:
            # Full save - update structure_updated_at
            self.structure_updated_at = timezone.now()

        # Ensure quantity_updated_at is always updated for quantity changes
        if update_fields:
            has_quantity_update = any(field in update_fields for field in ['stock', 'fabric_length'])
            if has_quantity_update and 'quantity_updated_at' not in update_fields:
                update_fields.append('quantity_updated_at')
                kwargs['update_fields'] = update_fields

        super().save(*args, **kwargs)

    def __str__(self):
        shop = self.product.shop_service.shop
        return f"{self.product.name} - {self.attribute or 'default'}({self.id}) - {shop.name}({shop.id})"
