from django.db import models
from .feature import Feature


class UserFeature(models.Model):

    role = models.CharField(
        max_length=20,
        choices=[
            ("OWNER", "Owner"),
            ("MANAGER", "Manager"),
            ("STAFF", "Staff"),
        ]
    )

    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        related_name="role_features"
    )

    class Meta:
        unique_together = ("role", "feature")

    def __str__(self):
        return f"{self.role} -> {self.feature.code}"
