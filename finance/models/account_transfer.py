from django.db import models
from safedelete.models import SafeDeleteModel, SOFT_DELETE


class AccountTransfer(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE

    shop = models.ForeignKey(
        'shop.Shop',
        on_delete=models.CASCADE,
        related_name='account_transfers'
    )

    from_account = models.ForeignKey(
        'finance.Account',
        on_delete=models.CASCADE,
        related_name='transfers_out'
    )

    to_account = models.ForeignKey(
        'finance.Account',
        on_delete=models.CASCADE,
        related_name='transfers_in'
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)

    transfer_date_time = models.DateTimeField()

    notes = models.TextField(null=True, blank=True)

    created_by = models.ForeignKey(
        'authentication.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['shop', 'transfer_date_time']),
            models.Index(fields=['shop', 'from_account']),
            models.Index(fields=['shop', 'to_account']),
        ]

    def __str__(self):
        return f"Transfer {self.amount} from {self.from_account_id} to {self.to_account_id}"
