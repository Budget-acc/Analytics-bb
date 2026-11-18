from django.db import models, transaction
from django.db.models import Max, UniqueConstraint
from django.db.models.functions import Cast, Substr
from django.core.validators import MinValueValidator, URLValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from shop.models import Shop
from authentication.models import User
from .vendor import Vendor


class Purchase(SafeDeleteModel):
    """
    Purchase model represents a purchase order from a vendor.
    Supports two types:
    1. With products - detailed item tracking with stock updates
    2. Without products - direct total entry without inventory tracking
    """
    _safedelete_policy = SOFT_DELETE

    PURCHASE_TYPE_CHOICES = [
        ('with_products', 'With Products'),
        ('without_products', 'Without Products'),
    ]

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="purchases",
        db_index=True
    )
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchases",
        db_index=True
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="purchases",
        help_text="User who created this purchase"
    )

    # Purchase Code - Auto-generated (e.g., PO000001), unique per shop (see
    # Meta.constraints) - not globally unique, matching Sale.shop_sale_id.
    purchase_code = models.CharField(max_length=10, blank=True, db_index=True)

    # Purchase Type
    purchase_type = models.CharField(
        max_length=20,
        choices=PURCHASE_TYPE_CHOICES,
        default='with_products',
        help_text="Purchase with product tracking or direct entry"
    )

    # Invoice and Date
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    purchase_date = models.DateTimeField(db_index=True)

    # Tax details (only meaningful for 'without_products' purchases - stored
    # explicitly regardless of whether purchase_total is tax-inclusive or
    # tax-exclusive, per the shop's own bookkeeping convention)
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Tax rate percentage applied (e.g. 18.00 for 18% GST)."
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Tax amount for this purchase."
    )

    # Financial Information
    # For 'with_products': auto-calculated from items
    # For 'without_products': manually entered
    purchase_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Total purchase amount before additional charges"
    )

    # Total quantity (only for 'without_products' type)
    total_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Total quantity purchased (for without_products type)"
    )

    # Additional charges total (sum of all AdditionalCharge records)
    additional_charges_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)]
    )

    # Grand Total = purchase_total + additional_charges_total
    grand_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)]
    )

    # Paid amount (sum of all PurchasePayment records)
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)]
    )

    # Documents (max 3 attachments, per product-owner spec)
    documents = models.JSONField(
        default=list,
        blank=True,
        help_text="List of S3 URLs for uploaded documents (max 3)"
    )

    # Notes
    notes = models.TextField(blank=True, null=True)

    # Completion Status
    is_completed = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp when this purchase was marked completed."
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "purchase"
        verbose_name = "Purchase"
        verbose_name_plural = "Purchases"
        constraints = [
            UniqueConstraint(fields=["shop", "purchase_code"], name="unique_purchase_code_per_shop"),
        ]
        indexes = [
            models.Index(fields=['shop', 'purchase_code']),
            models.Index(fields=['shop', 'purchase_date']),
            models.Index(fields=['shop', 'is_completed']),
            models.Index(fields=['vendor', 'purchase_date']),
            models.Index(fields=['purchase_type']),
        ]
        ordering = ['-purchase_date', '-created_at']

    def __str__(self):
        return f"Purchase {self.purchase_code} - {self.shop.name}"

    def clean(self):
        """Validate documents field (max 4 attachments)"""
        super().clean()
        if self.documents:
            if not isinstance(self.documents, (list, tuple)):
                raise ValidationError({'documents': 'Documents must be a list.'})

            if len(self.documents) > 3:
                raise ValidationError({'documents': 'Maximum 3 attachments allowed.'})

            url_validator = URLValidator()
            for idx, url in enumerate(self.documents):
                if not isinstance(url, str) or not url.strip():
                    raise ValidationError({
                        'documents': f'Document at index {idx} must be a non-empty string.'
                    })
                try:
                    url_validator(url)
                except ValidationError:
                    raise ValidationError({
                        'documents': f'Invalid URL at index {idx}: {url}'
                    })

    def save(self, *args, **kwargs):
        # Auto-generate purchase_code from a per-shop counter stored on
        # Shop.last_purchase_id - same pattern as BookingV2.shop_booking_id/
        # Sale.shop_sale_id/TailorOrder.shop_tailor_id. The shop row is
        # locked for the duration, so concurrent creates for the same shop
        # are fully serialized and codes are guaranteed to be unique per
        # shop (see Meta.constraints) without needing retries.
        is_new = self.pk is None
        if is_new and not self.purchase_code and self.shop_id:
            with transaction.atomic():
                from shop.models import ShopNumberingSequence
                sequence = (
                    ShopNumberingSequence.objects.select_for_update()
                    .filter(shop_id=self.shop_id).first()
                )
                if sequence is None:
                    # Normally already created by Shop.save()'s
                    # _ensure_product_code_prefix() hook - this is just a
                    # defensive fallback for a shop that predates that hook
                    # and hasn't been re-saved since.
                    sequence = ShopNumberingSequence.objects.create(shop_id=self.shop_id)
                    sequence = ShopNumberingSequence.objects.select_for_update().get(pk=sequence.pk)

                # Always reconcile against the table's real current max
                # before trusting the stored counter - not just once at
                # last_purchase_id == 0. Without this, any drift between
                # the counter and the table (re-seeded/restored data, a
                # direct insert, a prior counter reset) makes every
                # subsequent create for this shop collide against
                # unique_purchase_code_per_shop, one by one, until the
                # counter happens to catch up past the real max - this
                # makes that class of drift self-healing on the very next
                # create instead.
                # all_objects (not objects) - the unique_purchase_code_per_shop
                # constraint applies to soft-deleted rows too, so a
                # soft-deleted PO000005 must still count toward the max,
                # or the next generated code would collide with it.
                max_id = (
                    Purchase.all_objects.filter(
                        shop_id=self.shop_id,
                        purchase_code__regex=r"^PO\d{6}$"
                    )
                    .annotate(
                        purchase_id_int=Cast(Substr("purchase_code", 3), models.IntegerField())
                    )
                    .aggregate(max_id=Max("purchase_id_int"))
                )["max_id"] or 0

                base = max(sequence.last_purchase_id, max_id, sequence.purchase_start_id or 0)

                # Generate next purchase ID
                next_id = base + 1
                sequence.last_purchase_id = next_id
                sequence.save(update_fields=["last_purchase_id"])

                self.purchase_code = f"PO{next_id:06d}"

        # Calculate grand_total
        self.grand_total = self.purchase_total + self.additional_charges_total

        super().save(*args, **kwargs)

    @property
    def outstanding_balance(self):
        """Remaining amount owed to the vendor (purchase_total basis - excludes
        additional_charges, which are separately-ledgered on-site expenses
        already settled at the time they're incurred, not part of the
        vendor's pending balance)."""
        return self.purchase_total - self.paid_amount

    @property
    def is_fully_paid(self):
        """Check if the vendor bill (purchase_total) is fully paid."""
        return self.paid_amount >= self.purchase_total
