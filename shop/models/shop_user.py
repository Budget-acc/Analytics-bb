from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class ShopUser(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    ROLE_CHOICES = [
        ("OWNER", "Owner"),
        ("MANAGER", "Manager"),
        ("STAFF", "Staff"),
    ]

    shop = models.ForeignKey(
        "Shop", on_delete=models.CASCADE, related_name="shop_users"
    )
    user = models.ForeignKey(
        "authentication.User", on_delete=models.CASCADE, related_name="shop_memberships"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("shop", "user")

    def __str__(self):
        return (
            f"{self.user.full_name or self.user.phone} - {self.shop.name} - {self.role}"
        )
