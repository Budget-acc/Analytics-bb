import uuid
from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .product import Product
from .product_variant import ProductVariant


class ProductTransferHistory(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    TRANSFER_STATUS_CHOICES = [
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
        ("PENDING", "Pending"),
    ]

    # Transfer details
    transfer_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    status = models.CharField(max_length=20, choices=TRANSFER_STATUS_CHOICES, default="COMPLETED")

    # Source information
    source_shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="outgoing_transfers"
    )
    source_product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="source_transfers"
    )
    source_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="source_transfers"
    )

    # Destination information
    destination_shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="incoming_transfers"
    )
    destination_product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="destination_transfers",
        null=True, blank=True
    )
    destination_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="destination_transfers",
        null=True, blank=True
    )

    # Transfer details
    quantity_transferred = models.PositiveIntegerField()
    transfer_type = models.CharField(
        max_length=20,
        choices=[
            ("TO_EXISTING", "To Existing Product"),
            ("CREATE_NEW", "Create New Product")
        ]
    )

    # User and timestamps
    initiated_by = models.ForeignKey(
        "authentication.User",
        on_delete=models.CASCADE,
        related_name="initiated_transfers"
    )
    transfer_date = models.DateTimeField(auto_now_add=True)

    # Product snapshot at time of transfer
    product_snapshot = models.JSONField(
        null=True, blank=True,
        help_text="Snapshot of product details at time of transfer"
    )

    class Meta:
        indexes = [
            models.Index(fields=["transfer_id"]),
            models.Index(fields=["source_shop", "transfer_date"]),
            models.Index(fields=["destination_shop", "transfer_date"]),
            models.Index(fields=["initiated_by", "transfer_date"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-transfer_date"]

    def __str__(self):
        return f"Transfer {self.transfer_id} - {self.quantity_transferred} units from {self.source_shop.name} to {self.destination_shop.name}"
