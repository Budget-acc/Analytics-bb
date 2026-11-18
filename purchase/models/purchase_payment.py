from django.db import models
from django.core.validators import MinValueValidator
from .purchase import Purchase


class PurchasePayment(models.Model):
    """
    PurchasePayment tracks payments to vendors.
    Stored separately from the purchase.
    Outstanding balance is auto-calculated.
    """
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('upi', 'UPI'),
        ('bank', 'Bank Transfer'),
        ('cheque', 'Cheque'),
    ]

    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="payments",
        db_index=True
    )

    # Payment details
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0.01)]
    )
    # Real ledger account this payment was made from (drives create_ledger_entries /
    # delete_ledger_entries / update_daily_balance, same pattern as PaymentDetailsV2.account).
    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_payments",
    )
    # Legacy free-text account label (kept for any existing display value / backward compat).
    account_label = models.CharField(max_length=100, blank=True, null=True)
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='cash'
    )
    payment_date = models.DateTimeField(db_index=True)

    # Additional information
    notes = models.TextField(blank=True, null=True)
    transaction_reference = models.CharField(max_length=100, blank=True, null=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "purchase_payment"
        verbose_name = "Purchase Payment"
        verbose_name_plural = "Purchase Payments"
        indexes = [
            models.Index(fields=['purchase', 'payment_date']),
            models.Index(fields=['payment_method']),
        ]
        ordering = ['-payment_date', '-created_at']

    def __str__(self):
        return f"Payment {self.amount} for {self.purchase.purchase_code}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Update purchase paid_amount
        self._update_purchase_paid_amount()

    def delete(self, *args, **kwargs):
        purchase = self.purchase
        super().delete(*args, **kwargs)
        # Update purchase paid_amount
        self._update_purchase_paid_amount(purchase)

    def _update_purchase_paid_amount(self, purchase=None):
        """Recalculate and update the total paid amount for the purchase"""
        from django.db.models import Sum

        if not purchase:
            purchase = self.purchase

        total_paid = PurchasePayment.objects.filter(
            purchase=purchase
        ).aggregate(total=Sum('amount'))['total'] or 0

        Purchase.objects.filter(id=purchase.id).update(paid_amount=total_paid)
