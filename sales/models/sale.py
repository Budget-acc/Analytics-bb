from django.db import models, transaction
from django.utils import timezone
from django.db.models import UniqueConstraint, Max
from django.db.models.functions import Cast, Substr
from phonenumber_field.modelfields import PhoneNumberField


class Sale(models.Model):

    TAX_CALCULATION_TYPE_CHOICES = (
        ("exclusive", "Exclusive"),
        ("inclusive", "Inclusive"),
    )

    shop = models.ForeignKey(
        "shop.Shop", on_delete=models.CASCADE, related_name="sales"
    )
    client = models.ForeignKey(
        "booking_v2.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
        help_text="Client who made the purchase (optional)",
    )
    client_phone = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        help_text="Client phone number for the sale"
    )

    client_phone_e164 = PhoneNumberField(
        region="IN",
        null=True,
        blank=True,
        db_index=True,
        help_text="Client phone number in E164 format (e.g., +919876543210)"
    )

    address = models.TextField(null=True, blank=True)
    description = models.TextField(blank=True, null=True, help_text="Sale notes or description")
    sale_date = models.DateField(default=timezone.now, help_text="The date when the sale occurred")

    total_amount = models.PositiveIntegerField(default=0, help_text="Total sale value before discount")
    discount = models.PositiveIntegerField(default=0, help_text="Any discount applied")
    paid_amount = models.PositiveIntegerField(default=0, help_text="Total after discount")

    decrease_stock = models.BooleanField(default=False, help_text="Whether stock was decreased for this sale")

    created_at = models.DateTimeField(default=timezone.now)

    shop_sale_id = models.CharField(
        max_length=10, db_index=True, null=True, blank=True,
        help_text="Shop-specific Sale ID (incremental per shop)"
    )
    staff = models.ForeignKey(
        "shop.Staff", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales"
    )

    # --- Tax snapshot fields ---
    # Copied from ShopTaxConfiguration at the time tax is calculated (not a
    # live FK) so historical sales are unaffected by later changes to the
    # shop's tax configuration. All default to zero/blank/False, so existing
    # rows and any code path that doesn't populate them behave exactly as
    # before (no tax applied).
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_name = models.CharField(max_length=30, blank=True, default="")
    tax_calculation_type = models.CharField(
        max_length=15,
        choices=TAX_CALCULATION_TYPE_CHOICES,
        blank=True,
        default="",
    )
    is_tax_applied = models.BooleanField(default=False)
    # Snapshot of ShopTaxConfiguration.taxable_components at creation time, so
    # later changes to the shop's live tax config never retroactively affect
    # which parts of this sale are taxed on subsequent updates.
    taxable_components = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=["shop", "shop_sale_id"], name="unique_shop_sale_id"),
        ]
        indexes = [
            models.Index(fields=["shop", "created_at"]),
            models.Index(fields=["shop", "shop_sale_id"]),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if is_new and not self.shop_sale_id and self.shop_id:
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

                if sequence.last_sale_id == 0:
                    max_id = (
                        Sale.objects.filter(shop_id=self.shop_id, shop_sale_id__regex=r"^SL\d{6}$")
                        .annotate(shop_sale_id_int=Cast(Substr("shop_sale_id", 3), models.IntegerField()))
                        .aggregate(max_id=Max("shop_sale_id_int"))
                    )["max_id"] or 0

                    if max_id > 0:
                        sequence.last_sale_id = max_id
                    else:
                        sequence.last_sale_id = sequence.sale_start_id or 0

                next_id = sequence.last_sale_id + 1
                sequence.last_sale_id = next_id
                sequence.save(update_fields=["last_sale_id"])

                self.shop_sale_id = f"SL{next_id:06d}"

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Sale #{self.id} - {self.shop.name} ({self.total_amount})"
