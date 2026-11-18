from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .booking_v2 import BookingV2


class BookedProductVariant(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    RETURN_STATUS_CHOICES = [
        ("not_returned", "Not Returned"),
        ("returned", "Returned"),
    ]

    booking = models.ForeignKey(
        BookingV2, on_delete=models.CASCADE, related_name="booked_items"
    )
    product_variant = models.ForeignKey(
        "services.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True
    )
    user = models.ForeignKey("authentication.User", on_delete=models.CASCADE)
    shop = models.ForeignKey("shop.Shop", on_delete=models.CASCADE)

    amount = models.IntegerField()
    quantity = models.PositiveIntegerField(default=1, null=True, blank=True)

    return_status = models.CharField(
        max_length=20,
        choices=RETURN_STATUS_CHOICES,
        default="not_returned",
        db_index=True,
    )
    returned_at = models.DateTimeField(null=True, blank=True)
    extended_until = models.DateTimeField(null=True, blank=True)

    # Backup fields
    product_name = models.CharField(max_length=500)
    variant_attribute = models.CharField(max_length=50, null=True, blank=True)
    color = models.CharField(max_length=50, null=True, blank=True)
    category = models.CharField(max_length=50, null=True, blank=True)
    model = models.CharField(max_length=50, null=True, blank=True)
    product_thumbnail = models.URLField(max_length=500, null=True, blank=True)

    measurements = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.product_variant:
            # Safely set product_name
            if self.product_variant.product and self.product_variant.product.name:
                self.product_name = self.product_variant.product.name[:500]
            else:
                self.product_name = ""

            # Safely set variant_attribute
            if self.product_variant.attribute:
                self.variant_attribute = self.product_variant.attribute[:50]
            else:
                self.variant_attribute = ""

            # Safely set color
            if self.product_variant.product and self.product_variant.product.color:
                self.color = self.product_variant.product.color[:50]
            else:
                self.color = ""

            # Safely set category
            if self.product_variant.product and self.product_variant.product.category:
                self.category = self.product_variant.product.category[:50]
            else:
                self.category = ""

            # Safely set model
            if self.product_variant.product and self.product_variant.product.model:
                self.model = self.product_variant.product.model[:50]
            else:
                self.model = ""

            # Store the original URL
            if (not self.product_thumbnail and
                self.product_variant.product and
                self.product_variant.product.thumbnail_image):
                self.product_thumbnail = self.product_variant.product.thumbnail_image

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Booking #{self.booking.id} - {self.product_name} ({self.variant_attribute})"

    class Meta:
        indexes = [
            models.Index(fields=["shop"]),
            models.Index(fields=["user"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["shop", "created_at"], name="bpv_shop_created_idx"),
        ]
