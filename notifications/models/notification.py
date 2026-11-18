from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


class Notification(models.Model):

    TYPE_CHOICES = [
        ("booking", "Booking"),
        ("sale", "Sale"),
        ("expiry", "Expiry"),
        ("stock", "Stock"),
        ("tailor", "Tailor"),
        ("system", "System"),
        ("whatsapp_limit", "WhatsApp Limit Exceeded"),
    ]

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
    ]

    title = models.CharField(max_length=255)
    body = models.TextField()
    data = models.JSONField(blank=True, null=True)
    image_url = models.URLField(blank=True, null=True)  
    created_by = models.ForeignKey('authentication.User', on_delete=models.SET_NULL, null=True, blank=True, related_name="created_notifications")
    
    shop = models.ForeignKey(
        'shop.Shop',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications"
    )

    notification_type = models.CharField(
        max_length=50,
        choices=TYPE_CHOICES,
        default="system"
    )

    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default="normal"
    )

    # Polymorphic relation (important for redirect)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    object_id = models.PositiveIntegerField(
        null=True,
        blank=True
    )

    content_object = GenericForeignKey("content_type", "object_id")    
    
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    target = models.CharField(max_length=50, default="all")  # e.g., "all", "shop", "user", etc.

    class Meta:
        indexes = [
            models.Index(fields=["shop"]),
            models.Index(fields=["notification_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.title
