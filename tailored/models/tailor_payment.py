from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE
from .tailor import Tailor


class TailorPayment(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    PAYMENT_METHOD_CHOICES = [
        ("cash", "Cash"),
        ("upi", "UPI"),
    ]

    shop = models.ForeignKey(
        'shop.Shop',
        on_delete=models.CASCADE,
        related_name='tailor_payments'
    )

    tailor = models.ForeignKey(
        Tailor,
        on_delete=models.CASCADE,
        related_name='payments'
    )

    created_by = models.ForeignKey(
        'authentication.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    transaction_date_time = models.DateTimeField()

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    account = models.ForeignKey(
        'finance.Account',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='tailor_payments'
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES
    )

    category = models.ForeignKey(
        'expenses_v2.ShopCategory',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='tailor_payments'
    )

    notes = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['shop', 'tailor']),
            models.Index(fields=['shop', 'transaction_date_time']),
            models.Index(fields=['shop', 'account']),
            models.Index(fields=['tailor', 'transaction_date_time']),
        ]

    def __str__(self):
        return f"Payment to {self.tailor} - {self.amount}"
