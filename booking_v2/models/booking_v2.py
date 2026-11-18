from django.db import transaction, models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, URLValidator
from django.db.models.functions import Cast
from django.db.models import Max
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .client_v2 import ClientV2


class BookingV2(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    BOOKING_STATUS_CHOICES = [
        ("upcoming", "Upcoming"),
        ("completed", "Completed"),
    ]

    PURCHASE_MODE_CHOICES = [
        ("normal", "Normal"),
        ("package", "Package"),
        ("courier", "Courier"),
        ("pickup", "Pickup"),
    ]

    DELIVERY_STATUS_CHOICES = [
        ("booked", "Booked"),
        ("ready to deliver", "Ready to deliver"),
        ("delivered", "Delivered"),
        ("returned", "Returned"),
        ("cancelled", "Cancelled"),
    ]

    RENTAL_DURATION_TYPE = [
        ("daily", "Daily"),
        ("hourly", "Hourly"),
    ]

    TAX_CALCULATION_TYPE_CHOICES = (
        ("exclusive", "Exclusive"),
        ("inclusive", "Inclusive"),
    )

    user = models.ForeignKey(
        "authentication.User", on_delete=models.CASCADE, related_name="bookings"
    )
    shop = models.ForeignKey(
        "shop.Shop", on_delete=models.CASCADE, related_name="bookings"
    )
    client = models.ForeignKey(ClientV2, null=True, blank=True, on_delete=models.SET_NULL)
    service = models.ForeignKey(
        "services.ShopOwnerService", on_delete=models.SET_NULL, null=True, blank=True
    )

    total_amount = models.IntegerField()
    advance_amount = models.IntegerField()
    security_amount = models.BigIntegerField(null=True, blank=True)
    discount_amount = models.PositiveIntegerField(default=0, null=True, blank=True, validators=[MinValueValidator(0)])

    pickup_date = models.DateTimeField()
    return_date = models.DateTimeField(null=True, blank=True)
    pickup_time = models.TimeField(null=True, blank=True)
    return_time = models.TimeField(null=True, blank=True)
    original_pickup_date = models.DateTimeField(null=True, blank=True)
    original_return_date = models.DateTimeField(null=True, blank=True)

    purchase_mode = models.CharField(max_length=10, choices=PURCHASE_MODE_CHOICES)
    booking_status = models.CharField(
        max_length=10, choices=BOOKING_STATUS_CHOICES, default="upcoming"
    )
    payment_status = models.BooleanField(default=False)
    delivery_status = models.CharField(
        max_length=50,
        choices=DELIVERY_STATUS_CHOICES,
        null=True,
        blank=True,
        default="booked",
    )

    staff_name = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    details = models.JSONField(null=True, blank=True)
    client_address = models.TextField(null=True, blank=True)
    client_identity = models.ForeignKey("Client", null=True, blank=True, on_delete=models.SET_NULL)
    shop_booking_id = models.CharField(max_length=10, unique=False, db_index=True, null=True, blank=True)

    cooling_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date until which products remain unavailable after return (auto-set based on policy)."
    )

    staff = models.ForeignKey(
        "shop.Staff", on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings"
    )

    documents = models.JSONField(
        default=list,
        blank=True,
        help_text="List of S3 document image URLs"
    )
    documents_details = models.JSONField(default=dict, blank=True, null=True)

    rental_duration_type = models.CharField(
        max_length=10,
        choices=RENTAL_DURATION_TYPE,
        default="daily",
        help_text="Defines whether booking amount is calculated hourly or daily"
    )

    recorded_at = models.DateTimeField(auto_now_add=True)

    # --- Tax snapshot fields ---
    # Copied from ShopTaxConfiguration at the time tax is calculated (not a
    # live FK) so historical bookings are unaffected by later changes to the
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
    # which parts of this booking are taxed on subsequent updates.
    taxable_components = models.JSONField(default=list, blank=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        if is_new and not self.shop_booking_id and self.shop_id:
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

                if sequence.last_booking_id == 0:
                    # First-time setup: check existing max booking ID for this shop
                    max_id = (
                        BookingV2.objects.filter(
                            shop_id=self.shop_id,
                            shop_booking_id__regex=r"^\d{6}$"
                        )
                        .annotate(
                            shop_booking_id_int=Cast("shop_booking_id", models.IntegerField())
                        )
                        .aggregate(max_id=Max("shop_booking_id_int"))
                    )["max_id"] or 0

                    if max_id > 0:
                        sequence.last_booking_id = max_id
                    else:
                        sequence.last_booking_id = sequence.booking_start_id or 0

                # Generate next booking ID
                next_id = sequence.last_booking_id + 1
                sequence.last_booking_id = next_id
                sequence.save(update_fields=["last_booking_id"])

                self.shop_booking_id = f"{next_id:06d}"

        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        validator = URLValidator()

        if not isinstance(self.documents, (list, tuple)):
            raise ValidationError({"documents": "Documents must be a list of URL strings."})

        for i, url in enumerate(self.documents):
            if not isinstance(url, str) or not url.strip():
                raise ValidationError({"documents": f"Document at index {i} must be a valid string URL."})
            try:
                validator(url)
            except ValidationError:
                raise ValidationError({"documents": f"Document at index {i} is not a valid URL."})

    class Meta:
        indexes = [
            models.Index(fields=["shop", "shop_booking_id"]),
            models.Index(fields=["shop", "pickup_date"]),
            models.Index(fields=["shop", "created_at"]),
            models.Index(fields=["cooling_period_end"]),
            models.Index(fields=["pickup_date"]),
            models.Index(fields=["return_date"]),
            models.Index(fields=["booking_status"]),
        ]

    def __str__(self):
        return f"{self.user.full_name} - {self.shop.name} - {self.id}"
