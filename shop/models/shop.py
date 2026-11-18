import logging

from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from nanoid import generate

from shop.utils.s3_folder import allocate_s3_folder_name, legacy_s3_segment
from .mixins import AutoSlugPublicTokenMixin

logger = logging.getLogger(__name__)


def generate_public_token():
    return generate(size=12)


def generate_unique_public_token():
    """Generate a 12-char nanoid that does not already exist in the Shop table.
    12 chars distinguishes shop tokens from org tokens (10 chars) by length alone.
    """
    return Shop.generate_unique_public_token()

class Shop(AutoSlugPublicTokenMixin, SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE
    public_token_length = 12

    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=15, null=True, blank=True)
    phone2 = models.CharField(max_length=15, null=True, blank=True)
    address = models.TextField(null=True, blank=True)  # street
    gst_number = models.CharField(max_length=50, null=True, blank=True)
    img = models.CharField(max_length=500, null=True, blank=True)
    place = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    email = models.EmailField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=100, null=True, blank=True)
    pincode = models.CharField(max_length=10, null=True, blank=True)

    password = models.CharField(max_length=128, default="600600")
    password_changed_at = models.DateTimeField(null=True, blank=True)

    subscription_status = models.CharField(
        max_length=20,
        choices=[("ACTIVE", "Active"), ("EXPIRED", "Expired"), ("NONE", "None")],
        default="NONE",
    )
    subscribed_date = models.DateField(null=True, blank=True)
    renewal_due_date = models.DateField(null=True, blank=True)

    terms_and_conditions = models.JSONField(
        null=True,
        blank=True,
        help_text="List of bullet points for shop's terms and conditions",
    )

    extra_stock_limit = models.PositiveIntegerField(
        default=0,
        help_text="Additional product stock limit granted on top of plan limit"
    )

    booking_start_id = models.PositiveIntegerField(
        default=0,
        help_text="Custom starting ID for bookings (if provided at onboarding)."
    )
    sale_start_id = models.PositiveIntegerField(
        default=0,
        help_text="Custom starting ID for sales (if provided at onboarding)."
    )
    last_sale_id = models.PositiveIntegerField(default=0, help_text="Last generated sale ID")
    last_booking_id = models.PositiveIntegerField(default=0, help_text="Last generated booking ID")

    tailor_start_id = models.PositiveIntegerField(default=0, help_text="Custom starting ID for tailor orders (if provided at onboarding).")
    last_tailor_id = models.PositiveIntegerField(default=0, help_text="Last generated tailor order ID")

    purchase_start_id = models.PositiveIntegerField(default=0, help_text="Custom starting ID for purchases (if provided at onboarding).")
    last_purchase_id = models.PositiveIntegerField(default=0, help_text="Last generated purchase ID")

    last_product_number = models.PositiveIntegerField(
        default=0,
        help_text="Last generated product code number (numeric part only, no prefix). Used to generate Product.product_code."
    )

    is_active = models.BooleanField(default=True)

    extra_user_limit = models.PositiveIntegerField(
        default=0,
        help_text="Number of extra users allowed for this shop above the default user limit."
    )

    subscription_renewal_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Renewal price of the shop subscription at the time of creation."
    )

    slug = models.SlugField(
        max_length=255,
        blank=True,
        db_index=True,
    )

    public_token = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
    )

    public_listing_enabled = models.BooleanField(
        default=True,
        help_text="Allow customers to access shop products via QR code."
    )

    # ------------------------------------------------------------------
    # Organization (multi-branch / multi-brand) support
    # ------------------------------------------------------------------
    organization = models.ForeignKey(
        "shop.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shops",
        db_index=True,
        help_text="The organization (business group) this shop/branch belongs to.",
    )

    show_in_org_catalog = models.BooleanField(
        default=True,
        help_text="Whether this branch's products appear in the organization's public catalog.",
    )

    catalog_display_order = models.PositiveIntegerField(
        default=0,
        help_text="Display order of this branch in the organization's combined catalog (lower = first).",
    )

    show_prices_in_public_listing = models.BooleanField(
        default=False,
        help_text="Whether product prices are visible in the public product listing.",
    )

    s3_folder_name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        editable=False,
        db_index=True,
        help_text=(
            "Permanent top-level S3 folder for this shop's files. Assigned once at "
            "creation (or by backfill_shop_s3_folder_name); never changed on rename."
        ),
    )

    class Meta:  
        constraints = [
            UniqueConstraint(
                Lower("name"),
                Lower("place"),
                name="unique_shop_name_place_ci"
            )
        ]

        indexes = [
            models.Index(
                fields=["public_token", "is_active", "public_listing_enabled"],
                name="shop_public_lookup_idx",
            ),
            # shop_org_catalog_idx is intentionally omitted here.
            # The organization FK has db_index=True (single-column index).
            # A composite index on (organization, show_in_org_catalog, is_active)
            # can be added via a separate migration once the FK field is stable.
        ]

    def __str__(self):
        return f"{self.name} ({self.place}) - {self.id}"

    @property
    def s3_folder(self):
        """Top-level folder segment every shop-scoped S3 key must start with.

        Shops not yet backfilled fall back to the exact legacy name-derived
        folder, so their uploads keep going where they always did.
        """
        if self.s3_folder_name:
            return self.s3_folder_name
        # DEBUG: this runs on every upload (per image) for an unmapped shop, and
        # WARNING+ is posted synchronously to Discord by the root handler.
        # Unmapped shops are reported by `backfill_shop_s3_folder_name`.
        logger.debug(
            "Shop %s has no s3_folder_name; using legacy name-derived S3 folder", self.id
        )
        return legacy_s3_segment(self.name or f"shop_{self.id}")

    def save(self, *args, **kwargs):
        # Creation only - a rename must never move the shop to a new folder.
        if self._state.adding and not self.s3_folder_name:
            self.s3_folder_name = allocate_s3_folder_name(self.name, self.place)
        super().save(*args, **kwargs)
        self._ensure_product_code_prefix()

    def _ensure_product_code_prefix(self):
        """Auto-generates ShopNumberingSequence.product_code_prefix for this
        shop if it doesn't have one yet. Never overwrites an existing value
        (whether auto-generated earlier or manually set by an admin).

        Two concurrent shop creations can both read the same taken_prefixes
        snapshot before either commits and compute the same candidate, since
        product_code_prefix is only checked in-memory here (not locked) - the
        DB's unique=True constraint is the real backstop. On that collision,
        retry a few times with a freshly-read taken_prefixes set rather than
        letting the IntegrityError bubble up and fail the shop save.
        """
        from django.db import IntegrityError, transaction as db_transaction
        from .shop_numbering_sequence import ShopNumberingSequence
        from shop.utils.product_code_prefix import generate_unique_product_code_prefix

        sequence, _ = ShopNumberingSequence.objects.get_or_create(shop=self)
        if sequence.product_code_prefix:
            return

        def _load_taken_prefixes():
            return ShopNumberingSequence.objects.exclude(
                product_code_prefix__isnull=True
            ).exclude(pk=sequence.pk).values_list("product_code_prefix", flat=True)

        # Load once up front - only re-queried below after an actual collision,
        # not unconditionally on every loop iteration (that would re-scan the
        # whole table on every attempt, including the near-always-successful
        # first one, and amplify badly under concurrent shop creation).
        taken_prefixes = _load_taken_prefixes()

        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            sequence.product_code_prefix = generate_unique_product_code_prefix(
                self.name, self.place, taken_prefixes
            )
            try:
                with db_transaction.atomic():
                    sequence.save(update_fields=["product_code_prefix"])
                return
            except IntegrityError:
                # Another concurrent save() committed the same prefix first -
                # retry with a fresh taken_prefixes snapshot that now includes it.
                if attempt == max_attempts:
                    raise
                taken_prefixes = _load_taken_prefixes()
