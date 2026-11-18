from django.db import models
from .notification import Notification


class UserNotification(models.Model):

    PUSH_STATUS = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        "authentication.User",
        on_delete=models.CASCADE,
        related_name="user_notifications"
    )

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="user_notifications"
    )

    shop = models.ForeignKey(
        "shop.Shop",
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    is_read = models.BooleanField(default=False)

    is_deleted = models.BooleanField(default=False)

    push_status = models.CharField(
        max_length=20,
        choices=PUSH_STATUS,
        default="pending"
    )

    received_at = models.DateTimeField(auto_now_add=True)

    read_at = models.DateTimeField(null=True, blank=True)

    clicked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("user", "notification")

        indexes = [
            models.Index(fields=["user", "-received_at"]),
            models.Index(fields=["user", "is_read"]),
            models.Index(fields=["notification"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.notification.title}"
