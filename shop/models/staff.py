from django.db import models
from django.utils import timezone


class Staff(models.Model):
    shop = models.ForeignKey(
        "shop.Shop", on_delete=models.CASCADE, related_name="staff"
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15, db_index=True)

    color = models.CharField(max_length=8, null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(default=timezone.now)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["shop", "name"]),
            models.Index(fields=["phone"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.phone})"
