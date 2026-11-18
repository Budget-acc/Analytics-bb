from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .choices import FeatureKey, PasswordRequirement


class ShopFeaturePolicy(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey("Shop", on_delete=models.CASCADE, related_name="feature_policies")
    feature = models.CharField(max_length=50, choices=FeatureKey.choices)
    require_password_for = models.CharField(
        max_length=30,
        choices=PasswordRequirement.choices,
        default=PasswordRequirement.MANAGER_AND_STAFF,
        help_text="Who must enter the owner's password to use/see this feature."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("shop", "feature"),)
        indexes = [models.Index(fields=["shop", "feature"])]

    def __str__(self):
        return f"{self.shop.id} - {self.feature} -> {self.require_password_for}"
