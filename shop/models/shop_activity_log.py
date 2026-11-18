from django.db import models
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey


class ShopActivityLog(models.Model):
    shop = models.ForeignKey("shop.Shop", on_delete=models.CASCADE, related_name="activity_logs")
    user = models.ForeignKey("authentication.User", on_delete=models.SET_NULL, null=True, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")

    # Log details
    action = models.CharField(max_length=50)  # e.g. "created", "updated", "deleted", "status_changed"
    log_message = models.TextField()

    # Links a "restored" log back to its original "deleted" log for audit trail and UI restore-state tracking
    restored_from_activity = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="restore_logs",
    )

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shop"]),
            models.Index(fields=["action"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        ref = f"{self.content_type} ({self.object_id})" if self.content_type else "N/A"
        return f"{self.shop.name} - {self.action} - {ref}"
