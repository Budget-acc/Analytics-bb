
from django.db import models
from django.core.validators import MinValueValidator
from safedelete.models import SafeDeleteModel, SOFT_DELETE

from .marketplace_product import MarketplaceProduct
from .fabric import Fabric


class MarketplaceProductFabric(SafeDeleteModel):

    _safedelete_policy = SOFT_DELETE

    product = models.ForeignKey(
        MarketplaceProduct,
        on_delete=models.CASCADE,
        related_name="product_fabrics",
    )

    fabric = models.ForeignKey(
        Fabric,
        on_delete=models.PROTECT,
        related_name="product_fabrics",
    )

    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"{self.product.product_code} - "
            f"{self.fabric.name} - {self.selling_price}"
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "fabric"],
                name="unique_product_fabric",
            )
        ]
        ordering = ["id"]
