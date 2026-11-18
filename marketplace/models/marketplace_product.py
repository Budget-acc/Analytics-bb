
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from safedelete.models import SafeDeleteModel, SOFT_DELETE

from .marketplace_subcategory import MarketplaceSubCategory



class MarketplaceProduct(SafeDeleteModel):

    _safedelete_policy = SOFT_DELETE

    product_code = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
    )

    name = models.CharField(max_length=255)

    subcategory = models.ForeignKey(
        MarketplaceSubCategory,
        on_delete=models.PROTECT,
        related_name="products",
    )

    is_premium = models.BooleanField(default=False)

    color = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )

    # Keep the model field unchanged.
    model = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )

    description = models.TextField(
        blank=True,
        default="",
    )

    # Maximum 4 images.
    images = models.JSONField(
        default=list,
        blank=True,
    )

    # Maximum 1 video.
    videos = models.JSONField(
        default=list,
        blank=True,
    )

    # Purchase cost and selling price are now stored
    # nowhere on the product itself.
    size_options = models.JSONField(
        default=list,
        blank=True,
    )

    minimum_order_quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )

    maximum_order_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )

    is_public = models.BooleanField(default=True)

    show_selling_price = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)

    published_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.product_code} - {self.name}"

    class Meta:
            ordering = ["-id"]
            constraints = [
                models.CheckConstraint(
                    check=models.Q(
                        maximum_order_quantity__isnull=True
                    ) | models.Q(
                        maximum_order_quantity__gte=models.F('minimum_order_quantity')
                    ),
                    name='max_quantity_gte_min_quantity',
                ),
            ]


