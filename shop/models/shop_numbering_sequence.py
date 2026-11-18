from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class ShopNumberingSequence(SafeDeleteModel):
    """
    Shop-scoped ID/numbering counters, split out of Shop.

    Mirrors the following Shop fields (still present on Shop for now — see
    `shop.management.commands.backfill_shop_numbering_sequence` for the
    one-off copy command that populates this table from Shop):
    booking_start_id, last_booking_id, sale_start_id, last_sale_id,
    tailor_start_id, last_tailor_id, purchase_start_id, last_purchase_id,
    last_product_number.

    Also owns `product_code_prefix`, a shop-unique 5-character prefix for
    product codes, auto-generated on shop creation from name + place (see
    `shop.utils.product_code_prefix`) and manually overridable thereafter.
    This field has no Shop-model counterpart to mirror/backfill — it is new.
    """
    _safedelete_policy = SOFT_DELETE

    shop = models.OneToOneField(
        "Shop", on_delete=models.CASCADE, related_name="numbering_sequence"
    )

    booking_start_id = models.PositiveIntegerField(
        default=0,
        help_text="Custom starting ID for bookings (if provided at onboarding)."
    )
    last_booking_id = models.PositiveIntegerField(default=0, help_text="Last generated booking ID")

    sale_start_id = models.PositiveIntegerField(
        default=0,
        help_text="Custom starting ID for sales (if provided at onboarding)."
    )
    last_sale_id = models.PositiveIntegerField(default=0, help_text="Last generated sale ID")

    tailor_start_id = models.PositiveIntegerField(
        default=0,
        help_text="Custom starting ID for tailor orders (if provided at onboarding)."
    )
    last_tailor_id = models.PositiveIntegerField(default=0, help_text="Last generated tailor order ID")

    purchase_start_id = models.PositiveIntegerField(
        default=0,
        help_text="Custom starting ID for purchases (if provided at onboarding)."
    )
    last_purchase_id = models.PositiveIntegerField(default=0, help_text="Last generated purchase ID")

    last_product_number = models.PositiveIntegerField(
        default=0,
        help_text="Last generated product code number (numeric part only, no prefix). Used to generate Product.product_code."
    )

    product_code_prefix = models.CharField(
        max_length=5,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Auto-generated 5-character prefix for this shop's product codes "
            "(2 chars from shop name + 3 chars from place). Can be manually "
            "overridden by the shop admin; once set it is never regenerated."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.shop.name} - numbering sequence"
