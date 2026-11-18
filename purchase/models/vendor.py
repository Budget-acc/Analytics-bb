from django.db import models
from phonenumber_field.modelfields import PhoneNumberField
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class Vendor(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="vendors",
    )

    name = models.CharField(
        max_length=200
    )

    contact_number = PhoneNumberField(
        region="IN",
        blank=True,
        null=True,
    )

    email = models.EmailField(
        blank=True,
        null=True,
    )

    location = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    gst_number = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )

    notes = models.TextField(
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(
        default=True
    )

    created_by = models.ForeignKey(
        "authentication.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vendors_created",
    )

    updated_by = models.ForeignKey(
        "authentication.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vendors_updated",
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shop", "name"]),
            models.Index(fields=["shop", "is_active"]),
            models.Index(fields=["shop", "deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "name"],
                condition=models.Q(deleted__isnull=True),
                name="unique_vendor_name_per_shop",
            )
        ]

    def __str__(self):
        return self.name