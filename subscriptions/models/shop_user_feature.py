from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .feature import Feature


class ShopUserFeature(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop_user = models.ForeignKey(
        'shop.ShopUser',
        on_delete=models.CASCADE,
        related_name='custom_features'
    )
    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        related_name='assigned_users'
    )

    granted = models.BooleanField(
        default=True,
        help_text="True = extra access, False = deny access"
    )

    class Meta:
        unique_together = ("shop_user", "feature")

    def __str__(self):
        return f"{self.shop_user} -> {self.feature.code} ({'granted' if self.granted else 'blocked'})"
