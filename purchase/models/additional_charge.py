from django.db import models
from django.core.validators import MinValueValidator
from .purchase import Purchase


class AdditionalCharge(models.Model):
    """
    AdditionalCharge records extra charges for a purchase.
    Examples: Transport, Loading, Packaging, Delivery
    These charges are immediately recorded in the ledger.
    """
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="additional_charges",
        db_index=True
    )

    # Charge details
    charge_name = models.CharField(
        max_length=100,
        help_text="e.g., Transport, Loading, Packaging, Delivery"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)]
    )

    # Real ledger account this charge was paid from (drives create_ledger_entries /
    # delete_ledger_entries / update_daily_balance, same pattern as PaymentDetailsV2.account).
    account = models.ForeignKey(
        'finance.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_additional_charges",
    )
    # Legacy free-text account label (kept for any existing display value / backward compat).
    account_label = models.CharField(max_length=100, blank=True, null=True)

    # Payment method for this charge
    payment_method = models.CharField(
        max_length=20,
        choices=[
            ('cash', 'Cash'),
            ('upi', 'UPI'),
            ('bank', 'Bank Transfer'),
        ],
        default='cash'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "additional_charge"
        verbose_name = "Additional Charge"
        verbose_name_plural = "Additional Charges"
        indexes = [
            models.Index(fields=['purchase']),
        ]
        ordering = ['id']

    def __str__(self):
        return f"{self.charge_name} - {self.amount} for {self.purchase.purchase_code}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Update purchase additional_charges_total
        self._update_purchase_charges()

    def delete(self, *args, **kwargs):
        purchase = self.purchase
        super().delete(*args, **kwargs)
        # Update purchase additional_charges_total
        self._update_purchase_charges(purchase)

    def _update_purchase_charges(self, purchase=None):
        """Recalculate and update additional_charges_total (and grand_total,
        which depends on it) for the purchase."""
        from django.db.models import F, Sum

        if not purchase:
            purchase = self.purchase

        total_charges = AdditionalCharge.objects.filter(
            purchase=purchase
        ).aggregate(total=Sum('amount'))['total'] or 0

        # grand_total must be updated here too - this raw queryset update
        # bypasses Purchase.save() (which is the only other place
        # grand_total gets recomputed), so leaving it out would let
        # grand_total go stale every time a charge is added or removed.
        Purchase.objects.filter(id=purchase.id).update(
            additional_charges_total=total_charges,
            grand_total=F('purchase_total') + total_charges,
        )
