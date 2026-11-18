from django.db import models
from django.utils import timezone
from .booking_v2 import BookingV2


class SecurityDepositAdjustment(models.Model):

    ADJUSTMENT_TYPE_CHOICES = [
        ("refund", "Refund"),
        ("deduction", "Deduction"),
    ]

    booking = models.ForeignKey(
        BookingV2,
        on_delete=models.CASCADE,
        related_name="security_adjustments",
        db_index=True
    )

    adjustment_type = models.CharField(
        max_length=20,
        choices=ADJUSTMENT_TYPE_CHOICES,
        db_index=True
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)

    payment_method = models.CharField(
        max_length=20,
        choices=[
            ("cash", "Cash"),
            ("upi", "UPI"),
        ],
        null=True,
        blank=True,
        db_index=True
    )

    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )    

    reason = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_migration_generated = models.BooleanField(default=False)
    recorded_at = models.DateTimeField(auto_now_add=True)    

    class Meta:
        indexes = [
            models.Index(fields=["booking", "adjustment_type"]),
            models.Index(fields=["booking", "created_at"]),
        ]

    def __str__(self):
        return (
            f"{self.get_adjustment_type_display()} ₹{self.amount} "
            f"- Booking #{self.booking.id}"
        )
