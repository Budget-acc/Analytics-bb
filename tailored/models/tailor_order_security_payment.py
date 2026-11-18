from django.db import models
from django.utils import timezone
from .tailor_order import TailorOrder


class TailorOrderSecurityPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("upi", "UPI"),
        ("cash", "Cash"),
    ]

    order = models.ForeignKey(
        TailorOrder,
        on_delete=models.CASCADE,
        related_name="security_payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default="cash")

    is_paid = models.BooleanField(default=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    is_migration_generated = models.BooleanField(default=False)
    recorded_at = models.DateTimeField(auto_now_add=True)    

    class Meta:
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["account"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Security ₹{self.amount} for TailorOrder #{self.order_id}"
