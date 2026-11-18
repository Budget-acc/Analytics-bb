from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .feature import Feature


class ShopFeatureLimit(SafeDeleteModel):
    """
    Extra / overridden limits per shop
    """
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        related_name="feature_limits"
    )
    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE
    )

    extra_limit = models.IntegerField(
        help_text="+2000 or -1000"
    )

    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    reason = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.shop.name} - {self.feature.code} : {self.extra_limit}"
