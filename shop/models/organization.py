from django.db import models

from .mixins import AutoSlugPublicTokenMixin


def generate_unique_org_token():
    """Generate a nanoid that does not already exist in the Organization table."""
    return Organization.generate_unique_public_token()


class Organization(AutoSlugPublicTokenMixin, models.Model):
    public_token_length = 10

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, db_index=True)

    public_token = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        null=True,
        blank=True,
        help_text="Public access token for the organization-level combined listing.",
    )

    phone = models.CharField(max_length=15, null=True, blank=True)
    phone2 = models.CharField(max_length=15, null=True, blank=True)
    street = models.TextField(null=True, blank=True)
    gst_number = models.CharField(max_length=50, null=True, blank=True)
    place = models.CharField(max_length=100, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=100, null=True, blank=True)
    pincode = models.CharField(max_length=10, null=True, blank=True)
    image = models.CharField(max_length=500, null=True, blank=True)

    is_active = models.BooleanField(default=True)

    public_listing_enabled = models.BooleanField(
        default=True,
        help_text="Allow customers to access the combined organization product catalog.",
    )

    show_prices_in_public_listing = models.BooleanField(
        default=False,
        help_text="Whether product prices are visible in the org's public combined catalog.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["public_token", "is_active", "public_listing_enabled"],
                name="org_public_lookup_idx",
            ),
        ]

    def __str__(self):
        return self.name
