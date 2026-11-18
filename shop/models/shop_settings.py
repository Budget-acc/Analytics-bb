from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class ShopSettings(SafeDeleteModel):
    """
    Stores configurable policies/settings for a Shop.
    Example: cooling period, grace period, late fee policy, etc.
    """
    _safedelete_policy = SOFT_DELETE

    ACTION_CHOICES = [
        ("ASK_EVERY_TIME", "Ask Every Time"),
        ("SALES", "Sales"),
        ("BOOKINGS", "Bookings"),
        ("CUSTOMIZATION", "Customization"),
    ]

    COOLING_PERIOD_TYPE_CHOICES = [
        ("after", "After Return"),
        ("before", "Before Pickup"),
    ]

    PRINTER_PREFERENCE_CHOICES = [
        ("ASK_EVERY_TIME", "Ask Every Time"),
        ("INVOICE_PDF", "Invoice PDF"),
        ("RECEIPT", "Receipt"),
    ]

    shop = models.OneToOneField(
        "Shop", on_delete=models.CASCADE, related_name="settings"
    )
    cooling_days = models.PositiveIntegerField(
        default=0,
        help_text="Number of days after return when product remains unavailable (maintenance/cleaning period)."
    )

    default_action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        default="ASK_EVERY_TIME",
        help_text="Default action for sales or bookings."
    )
    search_client = models.BooleanField(default=False)

    cooling_period_type = models.CharField(
        max_length=10,
        choices=COOLING_PERIOD_TYPE_CHOICES,
        default="after",
        help_text="Whether cooling period is after return or before pickup."
    )

    printer_preference = models.CharField(
        max_length=20,
        choices=PRINTER_PREFERENCE_CHOICES,
        default="ASK_EVERY_TIME",
        help_text="Which document to print by default when a booking/sale is completed - or ask every time.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Settings for {self.shop.name} (Cooling Days: {self.cooling_days})"
