from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from .booking_v2 import BookingV2


class BookingAdditionalCharge(models.Model):
    booking = models.ForeignKey(
        BookingV2,
        on_delete=models.CASCADE,
        related_name="additional_charges",
        db_index=True
    )
    name = models.CharField(max_length=100, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)], db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["booking", "name"]),
            models.Index(fields=["booking", "amount"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.amount}"
