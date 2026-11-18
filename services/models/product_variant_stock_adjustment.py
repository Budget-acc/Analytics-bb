import uuid
from decimal import Decimal

from django.db import models
from django.db.models import Q

from .product import Product
from .product_variant import ProductVariant


class ProductVariantStockAdjustment(models.Model):
    """Append-only history of stock / fabric moved out of service and restored.

    ProductVariant keeps a single combined `unavailable_stock` /
    `unavailable_fabric_length`; this table keeps the reason for every
    movement plus before/after snapshots, so damage and maintenance remain
    distinguishable and any drift can be reconciled. Rows are never updated
    or deleted by the API; they go only when their shop / product / variant
    is hard-deleted (CASCADE), so admin hard deletes keep working.
    """

    class Action(models.TextChoices):
        MARK_UNAVAILABLE = "mark_unavailable", "Mark Unavailable"
        RESTORE = "restore", "Restore Availability"

    class Reason(models.TextChoices):
        DAMAGE = "damage", "Damage"
        MAINTENANCE = "maintenance", "Maintenance"
        OTHER = "other", "Other"

    batch_id = models.UUIDField(
        default=uuid.uuid4, editable=False, db_index=True,
        help_text="Shared by every row written in the same API request."
    )
    shop = models.ForeignKey("shop.Shop", on_delete=models.CASCADE, related_name="stock_adjustments")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_adjustments")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="stock_adjustments")
    action = models.CharField(max_length=20, choices=Action.choices)
    reason = models.CharField(max_length=20, choices=Reason.choices, null=True, blank=True)  # null: no reason given
    note = models.CharField(max_length=500, blank=True, default="")

    # Amount moved by this row.
    quantity = models.PositiveIntegerField(default=0)
    fabric_length = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # Snapshots of the variant before and after this row was applied.
    previous_stock = models.PositiveIntegerField()
    updated_stock = models.PositiveIntegerField()
    previous_unavailable_stock = models.PositiveIntegerField()
    updated_unavailable_stock = models.PositiveIntegerField()
    previous_fabric_length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    updated_fabric_length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    previous_unavailable_fabric_length = models.DecimalField(max_digits=10, decimal_places=2)
    updated_unavailable_fabric_length = models.DecimalField(max_digits=10, decimal_places=2)

    created_by = models.ForeignKey(
        "authentication.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stock_adjustments"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["product", "-created_at"]),
            models.Index(fields=["variant", "-created_at"]),
            models.Index(fields=["shop", "-created_at"]),
            models.Index(fields=["shop", "reason"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(quantity__gt=0) | Q(fabric_length__gt=0),
                name="stock_adjustment_amount_positive",
            ),
            models.CheckConstraint(
                check=Q(fabric_length__gte=0),
                name="stock_adjustment_fabric_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.get_action_display()} - variant {self.variant_id} ({self.get_reason_display()})"
