from django.db import models
from django.utils import timezone
from .booking_v2 import BookingV2


class RefundDetails(models.Model):

    REFUND_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("upi", "UPI"),
        ("bank", "Bank Transfer"),
    ]

    booking = models.ForeignKey(BookingV2, on_delete=models.CASCADE, related_name="refunds")
    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2)
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES)
    refund_reason = models.TextField(null=True, blank=True)
    refunded_by = models.ForeignKey("authentication.User", on_delete=models.SET_NULL, null=True, blank=True)
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
            models.Index(fields=["refund_method"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Refund of ₹{self.refunded_amount} for Booking #{self.booking.id}"
