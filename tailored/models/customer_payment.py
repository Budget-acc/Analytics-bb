from django.db import models
from django.utils import timezone
from .tailor_order import TailorOrder


class CustomerPayment(models.Model):

    PAYMENT_METHOD_CHOICES = [
        ("upi", "UPI"),
        ("cash", "Cash"),
    ]

    order = models.ForeignKey(
        TailorOrder, on_delete=models.CASCADE, related_name="payments"
    )
    payment_amount = models.IntegerField()
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES)

    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order.id} - {self.payment_amount}"
