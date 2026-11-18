from django.db import models
from django.utils import timezone
from .sale import Sale


class SalesPaymentDetails(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("gpay", "GPay"),
        ("cash", "Cash"),
        ("upi", "UPI"),
    ]

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="payments"
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

    def __str__(self):
        return f"{self.sale.id} - {self.payment_amount} via {self.payment_method}"
