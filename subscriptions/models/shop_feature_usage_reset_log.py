from django.conf import settings
from django.db import models
from .shop_feature_usage import ShopFeatureUsage


class ShopFeatureUsageResetLog(models.Model):
    """
    Audit log for every administrative reset of ShopFeatureUsage.used.

    Preserves the original usage value so resets can be reviewed and
    reversed if needed.  One row is written per reset action.
    """

    usage = models.ForeignKey(
        ShopFeatureUsage,
        on_delete=models.CASCADE,
        related_name="reset_logs",
    )
    reset_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="feature_usage_resets",
    )
    previous_value = models.PositiveIntegerField()
    new_value = models.PositiveIntegerField()
    reason = models.CharField(max_length=500, blank=True, default="")
    reset_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reset_at"]

    def __str__(self):
        return (
            f"Reset #{self.id} — usage {self.usage_id}: "
            f"{self.previous_value} → {self.new_value} by user {self.reset_by_id}"
        )
