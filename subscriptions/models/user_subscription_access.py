from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .feature import Feature
from django.conf import settings

class UserSubscriptionAccess(SafeDeleteModel):
    """Role-based access control within shop subscriptions"""
    _safedelete_policy = SOFT_DELETE

    shop_user = models.OneToOneField(
        'shop.ShopUser',
        on_delete=models.CASCADE,
        related_name='subscription_access'
    )

    # Feature access overrides
    allowed_features = models.ManyToManyField(
        Feature,
        blank=True,
        help_text="Additional features this user can access"
    )
    restricted_features = models.ManyToManyField(
        Feature,
        blank=True,
        related_name='restricted_users',
        help_text="Features this user cannot access even if shop has them"
    )

    # Access limits
    max_bookings_per_day = models.PositiveIntegerField(null=True, blank=True)
    can_view_analytics = models.BooleanField(default=False)
    can_manage_users = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.shop_user} - Access Control"

    def get_accessible_features(self, as_of=None):

        """Get features this user can access"""

        if getattr(settings, "ANALYTICS_MODE", False):
            return list(Feature.objects.all())  # analytics app: all features

        shop_features = set(self.shop_user.shop.subscription.get_all_features(as_of=as_of))
        additional_features = set(self.allowed_features.all())
        restricted_features = set(self.restricted_features.all())

        # Start with shop features + additional, then remove restricted
        accessible = (shop_features | additional_features) - restricted_features
        return list(accessible)
