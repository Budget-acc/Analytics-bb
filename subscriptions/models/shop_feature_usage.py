from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .feature import Feature


class ShopFeatureUsage(SafeDeleteModel):
    """
    Tracks how much a shop has used a feature
    """
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="feature_usage"
    )
    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE
    )

    used = models.PositiveIntegerField(default=0)

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    class Meta:
        unique_together = ("shop", "feature", "period_start")

    def __str__(self):
        return f"{self.shop.name} - {self.feature.code} : {self.used}"
