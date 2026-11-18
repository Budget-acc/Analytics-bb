from django.db import models
from django.utils import timezone
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .shop_subscription import ShopSubscription
from .feature import Feature


class ShopFeatureSubscription(SafeDeleteModel):
    """Individual feature add-ons for shops with flexible pricing"""
    _safedelete_policy = SOFT_DELETE

    shop_subscription = models.ForeignKey(ShopSubscription, on_delete=models.CASCADE)
    feature = models.ForeignKey(Feature, on_delete=models.CASCADE)

    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()

    # Flexible pricing - actual price paid by this shop
    price_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Actual price paid by this shop for this feature"
    )

    # Track discount/markup
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Discount given from base price"
    )
    discount_reason = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text="Reason for discount (bulk purchase, loyalty, etc.)"
    )

    # Pricing metadata
    base_price_at_purchase = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Base price of feature when purchased (for audit)"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('shop_subscription', 'feature')

    def __str__(self):
        return f"{self.shop_subscription.shop.name} - {self.feature.name} - ₹{self.price_paid}"

    def get_runtime_is_active(self, as_of=None):
        """Compute active status at runtime without mutating DB state.

        Returns False if end_date has passed, regardless of the stored is_active flag.
        Mirrors ShopSubscription.get_runtime_status() for eventual-consistency reads.
        """
        as_of = as_of or timezone.now()
        if self.is_active and self.end_date and self.end_date <= as_of:
            return False
        return self.is_active

    def save(self, *args, **kwargs):
        # Auto-populate base price if not set
        if not self.base_price_at_purchase:
            self.base_price_at_purchase = self.feature.base_price
        super().save(*args, **kwargs)
