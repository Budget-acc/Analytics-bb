from django.db import models


class DeviceToken(models.Model):
    user = models.ForeignKey('authentication.User', on_delete=models.CASCADE, related_name='device_tokens')
    shop = models.ForeignKey('shop.Shop', on_delete=models.CASCADE, null=True, blank=True, related_name='device_tokens')
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=10, choices=(('android','android'),('ios','ios')))
    is_active = models.BooleanField(default=True)

    failed_attempts = models.IntegerField(default=0)
    last_notification_sent = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} - {self.token[:10]}"
