from django.db import models
from django.utils import timezone
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .booking_v2 import BookingV2


class PaymentDetailsV2(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    PAYMENT_METHOD_CHOICES = [
        ("gpay", "GPay"),
        ("cash", "Cash"),
        ("upi", "UPI")
    ]

    booking = models.ForeignKey(
        BookingV2, on_delete=models.CASCADE, related_name="payments"
    )
    payment_amount = models.IntegerField()
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES)
    created_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(auto_now_add=True)

    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    class Meta:
        indexes = [
            models.Index(fields=["booking"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.booking.id} - {self.payment_amount}"
