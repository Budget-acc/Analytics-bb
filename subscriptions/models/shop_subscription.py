from django.db import models
from django.db.models import Q
from django.utils import timezone
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .subscription_plan import SubscriptionPlan
from .feature import Feature


class ShopSubscription(SafeDeleteModel):
    """Active subscription for a shop"""
    _safedelete_policy = SOFT_DELETE

    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("EXPIRED", "Expired"),
        ("SUSPENDED", "Suspended"),
        ("CANCELLED", "Cancelled"),
    ]

    shop = models.OneToOneField(
        'shop.Shop',
        on_delete=models.CASCADE,
        related_name='subscription'
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)

    # Subscription details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE")
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()

    # Add-on features
    addon_features = models.ManyToManyField(
        Feature,
        through='ShopFeatureSubscription',
        blank=True,
        related_name='shop_subscriptions'
    )

    # Payment tracking
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_status = models.BooleanField(default=False)

    # Auto-renewal
    auto_renew = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.shop.name} - {self.plan.name} ({self.status})"

    def get_runtime_status(self, as_of=None):
        """Resolve status at runtime without mutating DB state.

        If a subscription is still marked ACTIVE in the database but its end_date
        has passed, expose it as EXPIRED in API responses.
        """
        as_of = as_of or timezone.now()

        if self.status == "ACTIVE" and self.end_date and self.end_date <= as_of:
            return "EXPIRED"

        return self.status

    def is_active(self, as_of=None):
        return self.get_runtime_status(as_of=as_of) == "ACTIVE"

    def get_all_features(self, as_of=None):
        """Get all currently valid features available to this shop."""
        as_of = as_of or timezone.now()
        features = set(self.plan.included_features.all())

        # Add individual add-on features
        features.update(
            self.addon_features.filter(
                shopfeaturesubscription__shop_subscription=self,
                shopfeaturesubscription__is_active=True,
                shopfeaturesubscription__start_date__lte=as_of,
            ).filter(
                Q(shopfeaturesubscription__end_date__isnull=True)
                | Q(shopfeaturesubscription__end_date__gt=as_of)
            )
        )

        return list(features)

    def get_feature_codes(self, as_of=None):
        """Get list of feature codes for easy permission checking"""
        return [f.code for f in self.get_all_features(as_of=as_of)]
