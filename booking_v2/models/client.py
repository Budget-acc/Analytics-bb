from django.db import models
from django.utils import timezone
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from phonenumber_field.modelfields import PhoneNumberField


class Client(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey("shop.Shop", on_delete=models.CASCADE, related_name="client_identities")
    client_name = models.CharField(max_length=50)
    client_phone_1 = models.BigIntegerField(null=True, blank=True)
    client_phone_2 = models.BigIntegerField(null=True, blank=True)
    phone_1_e164 = PhoneNumberField(
        region="IN",
        null=True,
        blank=True,
        db_index=True
    )

    phone_2_e164 = PhoneNumberField(
        region="IN",
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [
            ("shop", "client_name", "phone_1_e164"),
        ]

        indexes = [
            models.Index(fields=["shop", "client_name", "client_phone_1"]),
            models.Index(fields=["shop", "client_name", "phone_1_e164"]),
            models.Index(fields=["shop"]),
            models.Index(fields=["client_phone_1"]),
        ]

    def __str__(self):
        return f"{self.client_name} - {self.client_phone_1}"

    def save(self, *args, **kwargs):
        # Capitalize name and normalize phone before saving (India-specific: removes +91)
        self.client_name = self.client_name.strip().title()
        super().save(*args, **kwargs)
