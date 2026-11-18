from django.db import models
from django.utils import timezone
from safedelete.models import SafeDeleteModel, SOFT_DELETE

class LedgerEntry(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    DIRECTION_CHOICES = [
        ('in', 'Inflow'),
        ('out', 'Outflow'),
    ]

    TRANSACTION_TYPE_CHOICES = [
        ('booking_payment', 'Booking Payment'),
        ('sale_payment', 'Sale Payment'),
        ('expense', 'Expense'),
        ('product_expense', 'Product Expense'),
        ('security_in', 'Security Deposit In'),
        ('security_out', 'Security Refund/Deduction'),
        ('refund', 'Booking Refund'),
        ('transfer_in', 'Account Transfer In'),
        ('transfer_out', 'Account Transfer Out'),
        ('tailor_order_payment', 'Tailor Order Payment'),
        ('tailor_order_security_in', 'Tailor Security Deposit In'),
        ('tailor_security_refund', 'Tailor Security Deposit Out'),
        ('tailor_order_refund', 'Tailor Order Refund'),
        ('tailor_payment', 'Tailor Payment'),
        ('manual_income', 'Manual Income'),
        ('investment', 'Investment'),
        ('withdrawal', 'Withdrawal'),
        ('purchase', 'Purchase'),
        ('purchase_payment', 'Purchase Payment'),
        ('purchase_additional_charge', 'Purchase Additional Charge'),
    ]

    shop = models.ForeignKey('shop.Shop', on_delete=models.CASCADE, related_name='ledger_entries')

    account = models.ForeignKey(
        'Account',
        on_delete=models.CASCADE,
        related_name='ledger_entries'
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)

    # +amount for inflow, -amount for outflow
    signed_amount = models.DecimalField(max_digits=12, decimal_places=2)

    direction = models.CharField(
        max_length=10,
        choices=DIRECTION_CHOICES
    )

    transaction_type = models.CharField(
        max_length=50,
        choices=TRANSACTION_TYPE_CHOICES
    )

    payment_method = models.CharField(max_length=10)  # keep for backward compatibility

    # Reference to original object (PaymentDetailsV2, Expense, etc.)
    reference_id = models.IntegerField()
    reference_type = models.CharField(max_length=50)

    created_at = models.DateTimeField()

    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        'authentication.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    is_system_generated = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['created_at']),
        ]

    def save(self, *args, **kwargs):
        # Set created_at if not set
        if not self.created_at:
            self.created_at = timezone.now()

        # Auto-set signed_amount
        if self.direction == 'in':
            self.signed_amount = self.amount
        else:
            self.signed_amount = -self.amount

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.transaction_type} | {self.amount} | {self.account.name}"