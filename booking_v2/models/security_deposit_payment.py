from django.db import models
from django.utils import timezone
from .booking_v2 import BookingV2


class SecurityDepositPayment(models.Model):

    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("upi", "UPI"),
    ]

    booking = models.ForeignKey(
        BookingV2,
        on_delete=models.CASCADE,
        related_name="security_deposit_payments",
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, null=True, blank=True)

    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    is_paid = models.BooleanField(default=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    is_migration_generated = models.BooleanField(default=False)
    recorded_at = models.DateTimeField(auto_now_add=True)    

    class Meta:
        indexes = [
            models.Index(fields=["booking"]),
            models.Index(fields=["payment_method"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Security Deposit ₹{self.amount} for Booking #{self.booking.id}"
